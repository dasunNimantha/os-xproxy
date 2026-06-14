#!/usr/local/bin/php
<?php

/*
 * Copyright (C) 2025 OPNsense Community
 * All rights reserved.
 *
 * Coretun subscription updater (CLI).
 *
 * Fetches one or more subscription URLs, parses the proxy URIs they
 * contain and synchronises the corresponding server profiles in the
 * Coretun model: new servers are added, servers that disappeared from
 * the subscription are removed, manually added servers are left alone.
 * The active server is re-pointed when the provider rotates it away.
 *
 * Usage:
 *   subscription_sync.php due        update auto-update subscriptions whose interval elapsed
 *   subscription_sync.php all        update every enabled subscription now
 *   subscription_sync.php <uuid>     update one subscription now
 *
 * ("due"/"all" are used instead of --due/--all because configctl would
 * otherwise treat a leading "--" token as one of its own options.)
 *
 * Output: JSON summary on stdout (consumed by the API / configd).
 *
 * The URL is only contacted from here, on explicit request (cron tick,
 * watchdog or the UI button) — never implicitly elsewhere.
 */

@include_once('config.inc');

use OPNsense\Core\Config;
use OPNsense\Coretun\Coretun;

const STATE_FILE = '/usr/local/etc/coretun/subscriptions_state.json';
const LOG_FILE = '/var/log/coretun.log';
const FETCH_SCRIPT = '/usr/local/opnsense/scripts/coretun/subscription_fetch.py';

/* Server fields copied verbatim from the parser output into the model. */
const SERVER_FIELDS = [
    'enabled', 'description', 'protocol', 'address', 'port',
    'user_id', 'password', 'encryption', 'flow', 'transport',
    'transport_host', 'transport_path', 'security', 'sni',
    'fingerprint', 'alpn', 'reality_pubkey', 'reality_short_id', 'raw_uri',
];

function ct_log(string $msg): void
{
    $line = date('Y/m/d H:i:s') . ' coretun: ' . $msg . "\n";
    @file_put_contents(LOG_FILE, $line, FILE_APPEND);
}

/**
 * Parse a subscription's exclude list into normalised, lower-cased keywords
 * (comma-separated input, trimmed, empties dropped). mb_strtolower keeps the
 * match case-insensitive for non-ASCII words too.
 */
function parse_exclude_keywords($sub): array
{
    $raw = (string)$sub->exclude_keywords;
    if (trim($raw) === '') {
        return [];
    }
    $out = [];
    foreach (explode(',', $raw) as $kw) {
        $kw = trim($kw);
        if ($kw !== '') {
            $out[] = function_exists('mb_strtolower') ? mb_strtolower($kw, 'UTF-8') : strtolower($kw);
        }
    }
    return $out;
}

/** True if the server's name (description) contains any exclude keyword. */
function server_excluded(array $srv, array $keywords): bool
{
    if (empty($keywords)) {
        return false;
    }
    $hay = (string)($srv['description'] ?? '');
    $hay = function_exists('mb_strtolower') ? mb_strtolower($hay, 'UTF-8') : strtolower($hay);
    foreach ($keywords as $kw) {
        // mb_strpos handles UTF-8 offsets; substring (not whole-word) match by design.
        if (function_exists('mb_strpos') ? (mb_strpos($hay, $kw, 0, 'UTF-8') !== false) : (strpos($hay, $kw) !== false)) {
            return true;
        }
    }
    return false;
}

function load_state(): array
{
    if (!is_file(STATE_FILE)) {
        return [];
    }
    $raw = @file_get_contents(STATE_FILE);
    $data = json_decode((string)$raw, true);
    return is_array($data) ? $data : [];
}

function save_state(array $state): void
{
    @mkdir(dirname(STATE_FILE), 0755, true);
    $tmp = STATE_FILE . '.tmp';
    if (@file_put_contents($tmp, json_encode($state, JSON_PRETTY_PRINT)) !== false) {
        @rename($tmp, STATE_FILE);
    }
}

/**
 * Run the Python fetch+parse helper for one subscription URL.
 * Returns the decoded JSON array, or null on a hard failure.
 */
function fetch_subscription(string $mode, string $url): ?array
{
    $cmd = '/usr/local/bin/python3 ' . escapeshellarg(FETCH_SCRIPT) . ' '
         . escapeshellarg($mode) . ' ' . escapeshellarg($url) . ' 2>/dev/null';
    $out = shell_exec($cmd);
    if ($out === null) {
        return null;
    }
    $data = json_decode(trim((string)$out), true);
    return is_array($data) ? $data : null;
}

/**
 * Synchronise the servers of a single subscription into the model.
 *
 * @return array{changed:bool, report:array<string,mixed>}
 */
function sync_one(Coretun $mdl, string $uuid, $sub, array &$state, int $now): array
{
    $report = ['uuid' => $uuid, 'name' => (string)$sub->name];
    $mode = (string)$sub->base64_mode ?: 'auto';
    $url = (string)$sub->url;

    $parsed = fetch_subscription($mode, $url);
    $prevCount = isset($state[$uuid]['count']) ? (int)$state[$uuid]['count'] : 0;

    if ($parsed === null || !empty($parsed['fetch_error'])) {
        $err = ($parsed === null) ? 'fetch helper returned no data' : (string)$parsed['fetch_error'];
        $state[$uuid] = ['last_ts' => $now, 'status' => 'error', 'message' => $err, 'count' => $prevCount];
        $report['status'] = 'error';
        $report['message'] = $err;
        ct_log("subscription '" . (string)$sub->name . "' update failed: " . $err);
        return ['changed' => false, 'report' => $report];
    }

    $servers = isset($parsed['servers']) && is_array($parsed['servers']) ? $parsed['servers'] : [];
    $excludeKw = parse_exclude_keywords($sub);

    /* raw_uri -> server definition (from the freshly fetched list). Entries whose
       name matches an exclude keyword are dropped here, so they are never added and
       (being absent from $newByRaw) any already-imported ones get removed below. */
    $newByRaw = [];
    $excluded = 0;
    $hadAny = false;
    foreach ($servers as $srv) {
        if (!is_array($srv) || empty($srv['raw_uri'])) {
            continue;
        }
        $hadAny = true;
        if (server_excluded($srv, $excludeKw)) {
            $excluded++;
            continue;
        }
        $newByRaw[(string)$srv['raw_uri']] = $srv;
    }

    if (!$hadAny) {
        /* Treat an empty/garbled list as an error rather than wiping every server.
           Note: a list that parsed fine but was fully excluded is NOT an error. */
        $msg = 'no valid servers parsed (' . count($servers) . ' entries)';
        $state[$uuid] = ['last_ts' => $now, 'status' => 'error', 'message' => $msg, 'count' => $prevCount];
        $report['status'] = 'error';
        $report['message'] = $msg;
        ct_log("subscription '" . (string)$sub->name . "' update aborted: " . $msg);
        return ['changed' => false, 'report' => $report];
    }

    /* Catalogue current model state. */
    $existingForSub = []; // raw_uri -> node uuid (servers owned by this subscription)
    $allRaw = [];         // raw_uri -> node uuid (any origin, to avoid cross-sub duplicates)
    foreach ($mdl->servers->server->iterateItems() as $srvUuid => $node) {
        $raw = (string)$node->raw_uri;
        if ($raw !== '') {
            $allRaw[$raw] = $srvUuid;
        }
        if ((string)$node->source_subscription === $uuid && $raw !== '') {
            $existingForSub[$raw] = $srvUuid;
        }
    }

    $added = 0;
    $removed = 0;

    /* Add servers present in the subscription but not yet owned by it. */
    foreach ($newByRaw as $raw => $srv) {
        if (isset($existingForSub[$raw])) {
            continue;
        }
        if (isset($allRaw[$raw])) {
            /* Same link already exists (manual import or another sub) — don't steal it. */
            continue;
        }
        $node = $mdl->servers->server->Add();
        foreach (SERVER_FIELDS as $field) {
            if (array_key_exists($field, $srv) && $srv[$field] !== '' && $srv[$field] !== null) {
                $node->$field = (string)$srv[$field];
            }
        }
        $node->source_subscription = $uuid;
        $added++;
    }

    /* Remove servers owned by this subscription that vanished from the list. */
    foreach ($existingForSub as $raw => $srvUuid) {
        if (!isset($newByRaw[$raw])) {
            $mdl->servers->server->del($srvUuid);
            $removed++;
        }
    }

    $changed = ($added > 0 || $removed > 0);
    $msg = sprintf('%d total, +%d -%d', count($newByRaw), $added, $removed);
    if ($excluded > 0) {
        $msg .= sprintf(', %d excluded', $excluded);
    }
    $state[$uuid] = [
        'last_ts' => $now,
        'status' => 'ok',
        'message' => $msg,
        'count' => count($newByRaw),
    ];
    $report['status'] = 'ok';
    $report['added'] = $added;
    $report['removed'] = $removed;
    $report['total'] = count($newByRaw);
    $report['excluded'] = $excluded;
    if (!empty($parsed['errors'])) {
        $report['parse_errors'] = count($parsed['errors']);
    }
    ct_log("subscription '" . (string)$sub->name . "' updated: +$added -$removed" . ($excluded > 0 ? " ($excluded excluded)" : '') . ' (' . count($newByRaw) . ' total)');

    return ['changed' => $changed, 'report' => $report];
}

/**
 * Ensure general.active_server still points at an existing, enabled server.
 * Re-points to another enabled server when the active one was removed.
 *
 * @return bool whether the active selection changed
 */
function ensure_active_server(Coretun $mdl): bool
{
    $active = (string)$mdl->general->active_server;
    $firstEnabled = null;
    $activeStillValid = false;
    foreach ($mdl->servers->server->iterateItems() as $srvUuid => $node) {
        if ((string)$node->enabled !== '1') {
            continue;
        }
        if ($firstEnabled === null) {
            $firstEnabled = $srvUuid;
        }
        if ($srvUuid === $active) {
            $activeStillValid = true;
        }
    }
    if ($active !== '' && $activeStillValid) {
        return false;
    }
    if ($firstEnabled !== null) {
        $mdl->general->active_server = $firstEnabled;
        ct_log('active server re-pointed to ' . $firstEnabled);
        return true;
    }
    if ($active !== '') {
        $mdl->general->active_server = '';
        return true;
    }
    return false;
}

/**
 * Apply the new configuration without nesting a configd call (which would
 * deadlock when we are ourselves running inside a configd action). We run
 * the same steps the [reconfigure] action performs, detached.
 */
function detached_reconfigure(): void
{
    $cmd = '(/usr/local/bin/php /usr/local/opnsense/scripts/coretun/sync_gateway.php; '
         . '/usr/local/opnsense/scripts/coretun/service_control.py reconfigure) '
         . '> /dev/null 2>&1 &';
    exec($cmd);
}

/* --------------------------------------------------------------------- */

$target = $argv[1] ?? 'due';
/* accept both "due"/"all" (configctl-friendly) and legacy "--due"/"--all" */
$target = ltrim($target, '-');
$mdl = new Coretun();
$state = load_state();
$now = time();

$subs = [];
foreach ($mdl->subscriptions->subscription->iterateItems() as $uuid => $sub) {
    $subs[$uuid] = $sub;
}

$targets = [];
if ($target === 'all' || $target === 'due') {
    foreach ($subs as $uuid => $sub) {
        if ((string)$sub->enabled !== '1') {
            continue;
        }
        if ($target === 'due') {
            if ((string)$sub->auto_update !== '1') {
                continue;
            }
            $interval = max(1, (int)(string)$sub->update_interval) * 3600;
            $last = isset($state[$uuid]['last_ts']) ? (int)$state[$uuid]['last_ts'] : 0;
            if (($now - $last) < $interval) {
                continue;
            }
        }
        $targets[$uuid] = $sub;
    }
} elseif (isset($subs[$target])) {
    $targets[$target] = $subs[$target];
} else {
    echo json_encode(['result' => 'failed', 'message' => 'unknown subscription']);
    exit(0);
}

$changed = false;
$summary = ['result' => 'ok', 'updated' => []];
$activeBefore = (string)$mdl->general->active_server;

foreach ($targets as $uuid => $sub) {
    $res = sync_one($mdl, $uuid, $sub, $state, $now);
    $changed = $changed || $res['changed'];
    $summary['updated'][] = $res['report'];
}

if ($changed) {
    ensure_active_server($mdl);
    $mdl->serializeToConfig();
    Config::getInstance()->save();
}
save_state($state);

/*
 * Only restart the tunnel when the *active* server actually changed.
 * Existing server entries are never edited in place (they are keyed by
 * raw_uri), so if the provider rotated other servers but our active one
 * is still present there is no need to interrupt the live connection.
 */
$activeAfter = (string)$mdl->general->active_server;
$enabled = (string)$mdl->general->enabled === '1';
if ($enabled && $activeBefore !== $activeAfter) {
    detached_reconfigure();
    $summary['reconfigured'] = true;
}

echo json_encode($summary);
exit(0);
