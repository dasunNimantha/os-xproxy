<?php

/*
 * Copyright (C) 2025 OPNsense Community
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\Coretun\Api;

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Backend;

class SubscriptionsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'coretun';
    protected static $internalModelClass = 'OPNsense\Coretun\Coretun';

    private const STATE_FILE = '/usr/local/etc/coretun/subscriptions_state.json';
    private const UUID_RE = '/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i';

    /**
     * Read the runtime state file written by subscription_sync.php.
     * @return array<string, array<string, mixed>>
     */
    private function loadState(): array
    {
        if (!is_file(self::STATE_FILE)) {
            return [];
        }
        $data = json_decode((string)@file_get_contents(self::STATE_FILE), true);
        return is_array($data) ? $data : [];
    }

    /**
     * Count how many server profiles currently belong to each subscription.
     * @return array<string, int>
     */
    private function serverCountsBySubscription(): array
    {
        $counts = [];
        $mdl = $this->getModel();
        foreach ($mdl->servers->server->iterateItems() as $node) {
            $src = (string)$node->source_subscription;
            if ($src !== '') {
                $counts[$src] = ($counts[$src] ?? 0) + 1;
            }
        }
        return $counts;
    }

    public function searchItemAction()
    {
        $result = $this->searchBase(
            'subscriptions.subscription',
            ['enabled', 'name', 'url', 'auto_update', 'update_interval'],
            'name'
        );
        if (isset($result['rows']) && is_array($result['rows'])) {
            $state = $this->loadState();
            $counts = $this->serverCountsBySubscription();
            foreach ($result['rows'] as &$row) {
                $uuid = $row['uuid'] ?? '';
                $st = $state[$uuid] ?? null;
                $row['server_count'] = (string)($counts[$uuid] ?? 0);
                if ($st !== null) {
                    $row['last_status'] = (string)($st['status'] ?? '');
                    $row['last_message'] = (string)($st['message'] ?? '');
                    $row['last_update'] = !empty($st['last_ts'])
                        ? date('Y-m-d H:i', (int)$st['last_ts']) : '';
                } else {
                    $row['last_status'] = '';
                    $row['last_message'] = '';
                    $row['last_update'] = '';
                }
            }
        }
        return $result;
    }

    public function getItemAction($uuid = null)
    {
        return $this->getBase('subscription', 'subscriptions.subscription', $uuid);
    }

    public function setItemAction($uuid)
    {
        $result = $this->setBase('subscription', 'subscriptions.subscription', $uuid);
        $this->refreshCron($result);
        return $result;
    }

    public function addItemAction()
    {
        $result = $this->addBase('subscription', 'subscriptions.subscription');
        $this->refreshCron($result);
        return $result;
    }

    public function delItemAction($uuid)
    {
        $result = $this->delBase('subscriptions.subscription', $uuid);
        $this->refreshCron($result);
        return $result;
    }

    /**
     * Fetch a subscription now and synchronise its servers.
     * Triggered by the per-row "Update" button in the UI.
     */
    public function updateAction($uuid = null)
    {
        $result = ['result' => 'failed'];
        if (!$this->request->isPost()) {
            return $result;
        }
        if (!is_string($uuid) || !preg_match(self::UUID_RE, $uuid)) {
            $result['message'] = 'invalid subscription id';
            return $result;
        }
        $backend = new Backend();
        $response = trim($backend->configdRun('coretun subscription_update ' . escapeshellarg($uuid)));
        $parsed = json_decode($response, true);
        if (is_array($parsed)) {
            return $parsed;
        }
        $result['message'] = 'update failed';
        $result['raw'] = $response;
        return $result;
    }

    /**
     * Update every enabled subscription immediately (manual "Update all").
     */
    public function updateAllAction()
    {
        $result = ['result' => 'failed'];
        if (!$this->request->isPost()) {
            return $result;
        }
        $backend = new Backend();
        $response = trim($backend->configdRun('coretun subscription_update all'));
        $parsed = json_decode($response, true);
        return is_array($parsed) ? $parsed : ['result' => 'failed', 'raw' => $response];
    }

    /**
     * Regenerate the system crontab so coretun_cron() picks up changes
     * (subscriptions added/removed or auto-update toggled).
     */
    private function refreshCron($result): void
    {
        if (is_array($result) && isset($result['result'])
            && in_array($result['result'], ['saved', 'deleted'], true)) {
            (new Backend())->configdRun('cron restart');
        }
    }
}
