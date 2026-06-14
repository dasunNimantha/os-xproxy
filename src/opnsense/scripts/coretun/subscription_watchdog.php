#!/usr/local/bin/php
<?php

/*
 * Copyright (C) 2025 OPNsense Community
 * All rights reserved.
 *
 * Coretun connectivity watchdog (CLI, run from cron every few minutes).
 *
 * Probes the local SOCKS proxy. On sustained failure it actively tests every
 * other enabled server (each through a throwaway xray instance, see
 * probe_servers.py) and fails over to the lowest-latency working one. If no
 * server passes traffic it refreshes the subscription(s) so a fresh server
 * list can be fetched, then retries on the next tick. Cooldowns prevent
 * flapping and excessive subscription fetches.
 */

@include_once('config.inc');

use OPNsense\Core\Config;
use OPNsense\Coretun\Coretun;

const STATE_FILE = '/var/run/coretun_watchdog.json';
const LOG_FILE = '/var/log/coretun.log';
const ACTIVE_FLAG = '/var/run/coretun_service.active';
const PROBE_SCRIPT = '/usr/local/opnsense/scripts/coretun/probe_servers.py';

const FAILOVER_AFTER = 2;      /* consecutive failed probes before acting */
const REFRESH_COOLDOWN = 1800; /* min seconds between subscription refreshes */
const PROBE_TIMEOUT = 8;
const PROBE_URLS = [
    'http://www.gstatic.com/generate_204',
    'http://cp.cloudflare.com/generate_204',
];

function wd_log(string $msg): void
{
    @file_put_contents(LOG_FILE, date('Y/m/d H:i:s') . ' coretun-watchdog: ' . $msg . "\n", FILE_APPEND);
}

function load_state(): array
{
    $data = json_decode((string)@file_get_contents(STATE_FILE), true);
    return is_array($data) ? $data : ['fails' => 0, 'last_failover_ts' => 0, 'last_refresh_ts' => 0];
}

function save_state(array $s): void
{
    $tmp = STATE_FILE . '.tmp';
    if (@file_put_contents($tmp, json_encode($s)) !== false) {
        @rename($tmp, STATE_FILE);
    }
}

/** Probe connectivity through the SOCKS proxy. */
function probe(string $host, int $port): bool
{
    foreach (PROBE_URLS as $url) {
        $cmd = '/usr/local/bin/curl -s -o /dev/null --max-time ' . PROBE_TIMEOUT
             . ' --socks5-hostname ' . escapeshellarg($host . ':' . $port)
             . ' -w ' . escapeshellarg('%{http_code}') . ' ' . escapeshellarg($url) . ' 2>/dev/null';
        $code = trim((string)shell_exec($cmd));
        if ($code === '204' || $code === '200') {
            return true;
        }
    }
    return false;
}

function detached_reconfigure(): void
{
    exec('(/usr/local/bin/php /usr/local/opnsense/scripts/coretun/sync_gateway.php; '
        . '/usr/local/opnsense/scripts/coretun/service_control.py reconfigure) > /dev/null 2>&1 &');
}

/**
 * Run the probe helper that tests every other enabled server through a
 * throwaway xray instance. Returns the decoded JSON
 * ({"results":[...], "best":"<uuid|null>"}) or null when it couldn't run.
 */
function run_probe(string $excludeUuid): ?array
{
    $cmd = '/usr/local/bin/python3 ' . escapeshellarg(PROBE_SCRIPT)
         . ' --exclude ' . escapeshellarg($excludeUuid) . ' 2>/dev/null';
    $out = shell_exec($cmd);
    if ($out === null) {
        return null;
    }
    $data = json_decode(trim((string)$out), true);
    return is_array($data) ? $data : null;
}

/**
 * Pick the next enabled server to fail over to. Prefers a server from the
 * same subscription as the current active one, otherwise any other enabled
 * server. Returns its uuid or null when there is no alternative.
 * Used only as a fallback when the active probe helper cannot run.
 */
function pick_failover_server(Coretun $mdl, string $activeUuid): ?string
{
    $activeSub = '';
    $enabled = [];
    foreach ($mdl->servers->server->iterateItems() as $uuid => $node) {
        if ((string)$node->enabled !== '1' || (string)$node->address === '') {
            continue;
        }
        $enabled[$uuid] = (string)$node->source_subscription;
        if ($uuid === $activeUuid) {
            $activeSub = (string)$node->source_subscription;
        }
    }
    /* same subscription first */
    foreach ($enabled as $uuid => $sub) {
        if ($uuid !== $activeUuid && $sub === $activeSub && $activeSub !== '') {
            return $uuid;
        }
    }
    foreach ($enabled as $uuid => $sub) {
        if ($uuid !== $activeUuid) {
            return $uuid;
        }
    }
    return null;
}

/* --------------------------------------------------------------------- */

$mdl = new Coretun();
if ((string)$mdl->general->enabled !== '1') {
    exit(0);
}
/* Don't act before the service has actually come up. */
if (!file_exists(ACTIVE_FLAG)) {
    exit(0);
}

$activeUuid = (string)$mdl->general->active_server;
if ($activeUuid === '') {
    exit(0);
}

$port = (int)(string)$mdl->general->socks_port;
if ($port <= 0 || $port > 65535) {
    $port = 10808;
}
$listen = (string)$mdl->general->socks_listen;
if ($listen === '' || $listen === '0.0.0.0' || $listen === '*' || $listen === '::') {
    $listen = '127.0.0.1';
}

$state = load_state();
$now = time();

if (probe($listen, $port)) {
    if (($state['fails'] ?? 0) > 0) {
        wd_log('connectivity restored');
    }
    $state['fails'] = 0;
    save_state($state);
    exit(0);
}

$state['fails'] = (int)($state['fails'] ?? 0) + 1;
wd_log('probe failed (' . $state['fails'] . ' consecutive)');

/* One grace cycle to ride out a transient blip before the (heavier) probe. */
if ($state['fails'] < FAILOVER_AFTER) {
    save_state($state);
    exit(0);
}

/*
 * Step 1: actively test every other enabled server and fail over to the
 * lowest-latency one that actually passes traffic. The current active server
 * is excluded — we already know it is failing.
 */
$probe = run_probe($activeUuid);
$best = (is_array($probe) && !empty($probe['best'])) ? (string)$probe['best'] : '';

if ($best === '' && $probe === null) {
    /* Probe helper could not run at all — fall back to a blind failover so we
       still switch to another server instead of getting stuck. */
    $best = (string)(pick_failover_server($mdl, $activeUuid) ?? '');
}

if ($best !== '' && $best !== $activeUuid) {
    /* Latency of the chosen server, for the log line. */
    $latMs = null;
    if (is_array($probe) && !empty($probe['results'])) {
        foreach ($probe['results'] as $r) {
            if (($r['uuid'] ?? '') === $best && isset($r['latency_ms'])) {
                $latMs = (int)$r['latency_ms'];
                break;
            }
        }
    }
    $mdl->general->active_server = $best;
    $mdl->serializeToConfig();
    Config::getInstance()->save();
    $state['fails'] = 0;                 /* give the new server a clean slate */
    $state['last_failover_ts'] = $now;
    save_state($state);
    wd_log('failover: switched to lowest-latency working server ' . $best
        . ($latMs !== null ? " (${latMs}ms)" : ''));
    detached_reconfigure();
    exit(0);
}

/*
 * Step 2: no other server passes traffic — refresh the subscription(s) to pull
 * a fresh server list, then retry on the next tick. Rate-limited so a real
 * outage (e.g. WAN down) doesn't hammer the providers.
 */
if (($now - (int)($state['last_refresh_ts'] ?? 0)) > REFRESH_COOLDOWN) {
    $subUuid = '';
    foreach ($mdl->servers->server->iterateItems() as $uuid => $node) {
        if ($uuid === $activeUuid) {
            $subUuid = (string)$node->source_subscription;
            break;
        }
    }
    /* Active server tied to a subscription -> refresh just that one; otherwise
       (e.g. a manually added active server) refresh every subscription. */
    $target = $subUuid !== '' ? $subUuid : 'all';
    $state['last_refresh_ts'] = $now;
    save_state($state);
    wd_log('no working server found; refreshing subscription (' . $target . ') to fetch fresh servers');
    exec('/usr/local/sbin/configctl coretun subscription_update ' . escapeshellarg($target) . ' > /dev/null 2>&1 &');
    exit(0);
}

save_state($state);
exit(0);
