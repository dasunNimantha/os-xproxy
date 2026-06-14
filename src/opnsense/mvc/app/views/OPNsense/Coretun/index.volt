{#
 # Copyright (C) 2025 OPNsense Community
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without
 # modification, are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright
 #    notice, this list of conditions and the following disclaimer in the
 #    documentation and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
 #}

<script>
    $(document).ready(function() {
        const data_get_map = {'frm_general_settings': "/api/coretun/settings/get"};
        var gridId = "#{{formGridServer['table_id']}}";
        var generalDirty = true;

        var excludeInterfaces = ['wan', 'lo0', 'coretun'];
        var tunFields = [
            'route_interfaces', 'tun_device', 'tun_address', 'tun_gateway', 'bypass_ips'
        ];

        function filterTunnelInterfaces() {
            var sel = $('#coretun\\.general\\.route_interfaces');
            sel.find('option').each(function() {
                var val = $(this).val();
                if (excludeInterfaces.indexOf(val) !== -1 || val.match(/^tun\d/)) {
                    $(this).remove();
                }
            });
            sel.attr('title', 'All interfaces (default)');
            sel.selectpicker('refresh');
        }

        function toggleTunFields() {
            var checked = $('#coretun\\.general\\.policy_route_lan').is(':checked');
            $.each(tunFields, function(_, fld) {
                var row = $('#coretun\\.general\\.' + fld).closest('tr');
                if (checked) {
                    row.show();
                } else {
                    row.hide();
                }
            });
        }

        function refreshGeneralForm() {
            generalDirty = false;
            return mapDataToFormUI(data_get_map).done(function() {
                formatTokenizersUI();
                filterTunnelInterfaces();
                $('.selectpicker').selectpicker('refresh');
                toggleTunFields();
                $('#coretun\\.general\\.policy_route_lan').off('change.tun').on('change.tun', toggleTunFields);
            });
        }

        function markGeneralDirty() {
            generalDirty = true;
        }

        refreshGeneralForm();

        $(gridId).UIBootgrid({
            search: '/api/coretun/servers/search_item',
            get: '/api/coretun/servers/get_item/',
            set: '/api/coretun/servers/set_item/',
            add: '/api/coretun/servers/add_item/',
            del: '/api/coretun/servers/del_item/',
            commands: {
                // "Measure latency" button in the grid footer.
                probe: {
                    method: function(event) {
                        doProbeServers();
                    },
                    classname: 'fa fa-fw fa-tachometer',
                    title: '{{ lang._("Measure latency") }}',
                    sequence: 50,
                    footer: true,
                    primary: false,
                    requires: []
                }
            },
            options: {
                formatters: {
                    // Renders the last probe result; tooltip shows when it was measured.
                    latency: function(column, row) {
                        var when = row.last_probe ? ' title="{{ lang._("measured") }} ' + escapeHtml(row.last_probe) + '"' : '';
                        if (row.last_latency === 'down') {
                            return '<span class="label label-danger"' + when + '>{{ lang._("down") }}</span>';
                        }
                        if (!row.last_latency || row.last_latency === '') {
                            return '<span class="text-muted">&mdash;</span>';
                        }
                        var ms = parseInt(row.last_latency, 10);
                        var cls = ms < 300 ? 'text-success' : (ms < 800 ? 'text-warning' : 'text-danger');
                        return '<span class="' + cls + '"' + when + '>' + ms + ' ms</span>';
                    }
                }
            }
        });

        var probeRunning = false;
        function doProbeServers() {
            if (probeRunning) {
                return;
            }
            probeRunning = true;
            var $icon = $('#servers').find('.command-probe span');
            var prevClass = $icon.attr('class');
            $icon.attr('class', 'fa fa-fw fa-spinner fa-pulse');
            ajaxCall('/api/coretun/servers/probe', {}, function(data, status) {
                probeRunning = false;
                $icon.attr('class', prevClass);
                $(gridId).bootgrid('reload');
                if (data && data.results) {
                    var working = data.results.filter(function(r) { return r.ok; });
                    var best = null;
                    for (var i = 0; i < data.results.length; i++) {
                        if (data.results[i].uuid === data.best) {
                            best = data.results[i];
                            break;
                        }
                    }
                    var msg = '{{ lang._("Measured") }} ' + data.results.length + ' {{ lang._("servers") }}, '
                            + working.length + ' {{ lang._("working") }}.';
                    if (best) {
                        msg += '<br/>{{ lang._("Fastest:") }} <b>' + escapeHtml(best.description) + '</b> ('
                             + best.latency_ms + ' ms)';
                    }
                    BootstrapDialog.alert({
                        type: working.length > 0 ? BootstrapDialog.TYPE_SUCCESS : BootstrapDialog.TYPE_WARNING,
                        title: '{{ lang._("Latency measurement") }}',
                        message: msg
                    });
                } else {
                    BootstrapDialog.alert('{{ lang._("Measurement failed.") }}');
                }
            });
        }

        $(gridId).on('loaded.rs.jquery.bootgrid', function() {
            markGeneralDirty();
        });

        // Subscriptions tab
        var subGridId = "#{{formGridSubscription['table_id']}}";

        function escapeHtml(s) {
            return $('<div/>').text(s == null ? '' : s).html();
        }

        function showUpdateResult(data) {
            if (!data) {
                BootstrapDialog.alert('{{ lang._("Update failed (no response).") }}');
                return;
            }
            var rows = data.updated || [];
            if (rows.length === 0 && data.result === 'failed') {
                BootstrapDialog.alert('{{ lang._("Update failed: ") }}' + escapeHtml(data.message || 'unknown'));
                return;
            }
            var msg = '';
            var anyErr = false;
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i];
                if (r.status === 'ok') {
                    msg += '<b>' + escapeHtml(r.name) + '</b>: ' + r.total + ' {{ lang._("servers") }} (+' + r.added + ' / -' + r.removed + ')';
                    if (r.excluded && r.excluded > 0) {
                        msg += ' <span class="text-muted">' + r.excluded + ' {{ lang._("excluded") }}</span>';
                    }
                    msg += '<br/>';
                } else {
                    anyErr = true;
                    msg += '<b>' + escapeHtml(r.name) + '</b>: <span class="text-danger">' + escapeHtml(r.message || 'error') + '</span><br/>';
                }
            }
            if (msg === '') {
                msg = '{{ lang._("Nothing to update.") }}';
            }
            BootstrapDialog.alert({
                type: anyErr ? BootstrapDialog.TYPE_WARNING : BootstrapDialog.TYPE_SUCCESS,
                title: '{{ lang._("Subscription update") }}',
                message: msg
            });
        }

        function refreshAfterUpdate() {
            $(subGridId).bootgrid('reload');
            $(gridId).bootgrid('reload');
            markGeneralDirty();
        }

        function doUpdateSubscription(uuid) {
            if (!uuid) {
                return;
            }
            ajaxCall('/api/coretun/subscriptions/update/' + uuid, {}, function(data, status) {
                showUpdateResult(data);
                refreshAfterUpdate();
            });
        }

        $(subGridId).UIBootgrid({
            search: '/api/coretun/subscriptions/search_item',
            get: '/api/coretun/subscriptions/get_item/',
            set: '/api/coretun/subscriptions/set_item/',
            add: '/api/coretun/subscriptions/add_item/',
            del: '/api/coretun/subscriptions/del_item/',
            // commands must be top-level: the framework registers them and wires the
            // click handler itself (the legacy 'loaded' binding doesn't run on the new grid).
            commands: {
                update: {
                    // method receives (event, cell); cell.getData() holds the row.
                    method: function(event, cell) {
                        doUpdateSubscription(cell.getData()['uuid']);
                    },
                    classname: 'fa fa-fw fa-refresh',
                    title: '{{ lang._("Update now") }}',
                    // before edit(100)/copy(200)/delete(500) in the commands column.
                    sequence: 50,
                    requires: []
                }
            },
            options: {
                formatters: {
                    boolean: function(column, row) {
                        return parseInt(row[column.id], 10) === 1
                            ? '<span class="fa fa-check text-success"></span>'
                            : '<span class="fa fa-times text-muted"></span>';
                    },
                    substatus: function(column, row) {
                        if (row.last_status === 'ok') {
                            return '<span class="label label-success">{{ lang._("ok") }}</span>';
                        }
                        if (row.last_status === 'error') {
                            return '<span class="label label-danger" title="' + escapeHtml(row.last_message) + '">{{ lang._("error") }}</span>';
                        }
                        return '<span class="text-muted">&mdash;</span>';
                    }
                }
            }
        });

        var updateAllRunning = false;
        $("#updateAllAct").click(function() {
            if (updateAllRunning) {
                return;
            }
            updateAllRunning = true;
            $("#updateAllAct").prop('disabled', true);
            $("#updateAllAct_progress").addClass("fa fa-spinner fa-pulse");
            ajaxCall('/api/coretun/subscriptions/update_all', {}, function(data, status) {
                $("#updateAllAct_progress").removeClass("fa fa-spinner fa-pulse");
                $("#updateAllAct").prop('disabled', false);
                updateAllRunning = false;
                showUpdateResult(data);
                refreshAfterUpdate();
            });
        });

        function updateServerDialogFields() {
            var dlg = $('#' + "{{formGridServer['edit_dialog_id']}}");
            var proto = dlg.find('#server\\.protocol').val();
            var security = dlg.find('#server\\.security').val();
            var transport = dlg.find('#server\\.transport').val();

            var showUuid = (proto === 'vless' || proto === 'vmess');
            var showPassword = (proto === 'shadowsocks' || proto === 'trojan');
            var showFlow = (proto === 'vless');
            var showEncryption = (proto === 'vless' || proto === 'vmess' || proto === 'shadowsocks');
            var showReality = (security === 'reality');
            var showTlsFields = (security === 'tls' || security === 'reality');
            var showTransportDetail = (transport === 'ws' || transport === 'h2' || transport === 'grpc' || transport === 'httpupgrade');

            dlg.find('#server\\.user_id').closest('.form-group').toggle(showUuid);
            dlg.find('#server\\.password').closest('.form-group').toggle(showPassword);
            dlg.find('#server\\.flow').closest('.form-group').toggle(showFlow);
            dlg.find('#server\\.encryption').closest('.form-group').toggle(showEncryption);
            dlg.find('#server\\.reality_pubkey').closest('.form-group').toggle(showReality);
            dlg.find('#server\\.reality_short_id').closest('.form-group').toggle(showReality);
            dlg.find('#server\\.sni').closest('.form-group').toggle(showTlsFields);
            dlg.find('#server\\.fingerprint').closest('.form-group').toggle(showTlsFields);
            dlg.find('#server\\.alpn').closest('.form-group').toggle(showTlsFields && !showReality);
            dlg.find('#server\\.transport_host').closest('.form-group').toggle(showTransportDetail);
            dlg.find('#server\\.transport_path').closest('.form-group').toggle(showTransportDetail);
        }

        $(document).on('change', '#server\\.protocol, #server\\.security, #server\\.transport', updateServerDialogFields);
        $(document).on('shown.bs.modal', '#' + "{{formGridServer['edit_dialog_id']}}", function() {
            setTimeout(updateServerDialogFields, 50);
        });

        $("#reconfigureAct").SimpleActionButton({
            onPreAction: function() {
                const dfObj = new $.Deferred();
                saveFormToEndpoint("/api/coretun/settings/set", 'frm_general_settings', function() {
                    dfObj.resolve();
                });
                return dfObj;
            }
        });

        updateServiceControlUI('coretun');

        // Import tab
        var importRunning = false;
        $("#importAct").click(function() {
            if (importRunning) {
                return;
            }
            var uris = $("#import_uris_text").val();
            if (!uris || uris.trim() === '') {
                BootstrapDialog.alert('{{ lang._("Please paste at least one proxy URI.") }}');
                return;
            }
            importRunning = true;
            $("#importAct").prop('disabled', true);
            $("#importAct_progress").addClass("fa fa-spinner fa-pulse");
            ajaxCall('/api/coretun/import/uris', {uris: uris}, function(data, status) {
                $("#importAct_progress").removeClass("fa fa-spinner fa-pulse");
                $("#importAct").prop('disabled', false);
                importRunning = false;
                if (status !== 'success' || data === undefined || data === null) {
                    BootstrapDialog.alert('{{ lang._("Import request failed (network or server error).") }}');
                    return;
                }
                if (data.result === 'saved') {
                    var msg = '{{ lang._("Imported") }} ' + data.count + ' {{ lang._("server(s).") }}';
                    if (data.skipped) {
                        msg += ' (' + data.skipped + ' {{ lang._("duplicate(s) skipped") }})';
                    }
                    if (data.auto_selected) {
                        msg += '<br/>{{ lang._("Auto-selected:") }} <b>' + data.auto_selected + '</b>';
                    }
                    if (data.errors && data.errors.length > 0) {
                        msg += '<br/><br/><small class="text-warning">{{ lang._("Parse errors:") }}<br/>';
                        for (var i = 0; i < data.errors.length && i < 10; i++) {
                            msg += '&bull; ' + $('<span/>').text(data.errors[i]).html() + '<br/>';
                        }
                        if (data.errors.length > 10) {
                            msg += '&hellip; ' + (data.errors.length - 10) + ' {{ lang._("more") }}';
                        }
                        msg += '</small>';
                    }
                    BootstrapDialog.alert({type: BootstrapDialog.TYPE_SUCCESS, message: msg});
                    $("#import_uris_text").val('');
                    $(gridId).bootgrid('reload');
                    markGeneralDirty();
                } else {
                    var errMsg = data.message || 'unknown error';
                    if (data.errors && data.errors.length > 0) {
                        errMsg += '<br/><br/><small>';
                        for (var j = 0; j < data.errors.length && j < 10; j++) {
                            errMsg += '&bull; ' + $('<span/>').text(data.errors[j]).html() + '<br/>';
                        }
                        errMsg += '</small>';
                    }
                    BootstrapDialog.alert('{{ lang._("Import failed: ") }}' + errMsg);
                }
            });
        });

        // Log tab
        var logTimer = null;
        var allowedHashes = ['#servers', '#subscriptions', '#general', '#import', '#log'];
        $('a[data-toggle="tab"]').on('shown.bs.tab', function(e) {
            var tab = $(e.target).attr('href');
            if (tab === '#log') {
                refreshLog();
                if (logTimer) {
                    clearInterval(logTimer);
                }
                logTimer = setInterval(refreshLog, 5000);
            } else {
                if (logTimer) {
                    clearInterval(logTimer);
                    logTimer = null;
                }
            }
            if (tab === '#servers') {
                $(gridId).bootgrid('reload');
            }
            if (tab === '#subscriptions') {
                $(subGridId).bootgrid('reload');
            }
            if (tab === '#general' && generalDirty) {
                refreshGeneralForm();
            }
            if (tab === '#servers' || tab === '#subscriptions' || tab === '#import' || tab === '#log') {
                $('#reconfigureAct').closest('.content-box').hide();
            } else {
                $('#reconfigureAct').closest('.content-box').show();
            }
        });

        function refreshLog() {
            ajaxGet('/api/coretun/service/log', {}, function(data, status) {
                if (status !== 'success') {
                    return;
                }
                if (data && data.response) {
                    var el = document.getElementById('coretun_log_output');
                    var atBottom = el && (el.scrollHeight - el.scrollTop - el.clientHeight < 30);
                    $("#coretun_log_output").text(data.response);
                    if (el && atBottom) {
                        el.scrollTop = el.scrollHeight;
                    }
                }
            });
        }

        $(window).on('beforeunload', function() {
            if (logTimer) {
                clearInterval(logTimer);
            }
        });

        var h = window.location.hash;
        if (h && allowedHashes.indexOf(h) !== -1) {
            $('a[href="' + h + '"]').trigger('click');
        }
        if (!h || h !== '#general') {
            $('#reconfigureAct').closest('.content-box').hide();
        }
        $('.nav-tabs a').on('shown.bs.tab', function(e) {
            history.pushState(null, null, e.target.hash);
        });
    });
</script>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li class="active"><a data-toggle="tab" id="tab_servers" href="#servers">{{ lang._('Servers') }}</a></li>
    <li><a data-toggle="tab" id="tab_subscriptions" href="#subscriptions">{{ lang._('Subscriptions') }}</a></li>
    <li><a data-toggle="tab" id="tab_general" href="#general">{{ lang._('General') }}</a></li>
    <li><a data-toggle="tab" id="tab_import" href="#import">{{ lang._('Import') }}</a></li>
    <li><a data-toggle="tab" id="tab_log" href="#log">{{ lang._('Log') }}</a></li>
</ul>

<div class="tab-content content-box">
    <div id="servers" class="tab-pane fade in active">
        {{ partial('layout_partials/base_bootgrid_table', formGridServer)}}
    </div>
    <div id="subscriptions" class="tab-pane fade in">
        <div class="col-md-12" style="padding-top: 10px;">
            <button class="btn btn-default pull-right" id="updateAllAct" type="button" style="margin-bottom: 8px;">
                <i class="fa fa-refresh"></i> {{ lang._('Update all now') }} <i id="updateAllAct_progress"></i>
            </button>
            <table id="{{formGridSubscription['table_id']}}" class="table table-condensed table-hover table-striped table-responsive"
                   data-store-selection="true"
                   data-editDialog="{{formGridSubscription['edit_dialog_id']}}">
                <thead>
                    <tr>
                        <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                        <th data-column-id="enabled" data-formatter="boolean" data-width="5em">{{ lang._('On') }}</th>
                        <th data-column-id="name" data-type="string">{{ lang._('Name') }}</th>
                        <th data-column-id="url" data-type="string">{{ lang._('URL') }}</th>
                        <th data-column-id="update_interval" data-type="string" data-width="8em">{{ lang._('Interval (h)') }}</th>
                        <th data-column-id="auto_update" data-formatter="boolean" data-width="5em">{{ lang._('Auto') }}</th>
                        <th data-column-id="last_update" data-type="string" data-width="11em">{{ lang._('Last update') }}</th>
                        <th data-column-id="last_status" data-formatter="substatus" data-width="7em">{{ lang._('Status') }}</th>
                        <th data-column-id="server_count" data-type="string" data-width="7em">{{ lang._('Servers') }}</th>
                        <th data-column-id="commands" data-sortable="false" data-width="11em">{{ lang._('Commands') }}</th>
                    </tr>
                </thead>
                <tbody></tbody>
                <tfoot>
                    <tr>
                        <td></td>
                        <td colspan="9">
                            <button data-action="add" type="button" class="btn btn-xs btn-primary" title="{{ lang._('Add') }}">
                                <span class="fa fa-plus fa-fw"></span>
                            </button>
                            <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default" title="{{ lang._('Delete selected') }}">
                                <span class="fa fa-trash-o fa-fw"></span>
                            </button>
                        </td>
                    </tr>
                </tfoot>
            </table>
        </div>
    </div>
    <div id="general" class="tab-pane fade in">
        {{ partial("layout_partials/base_form",['fields':formGeneral,'id':'frm_general_settings'])}}
    </div>
    <div id="import" class="tab-pane fade in">
        <div class="col-md-12" style="padding-top: 15px;">
            <div class="form-group">
                <label for="import_uris_text">{{ lang._('Proxy URIs') }}</label>
                <textarea class="form-control" id="import_uris_text" rows="8" style="resize: vertical;"
                          placeholder="{{ lang._('Paste proxy URIs here, one per line (vless://, vmess://, ss://, trojan://)') }}"></textarea>
            </div>
            <button class="btn btn-primary" id="importAct" type="button" style="margin-bottom: 15px;">
                <b>{{ lang._('Import') }}</b> <i id="importAct_progress"></i>
            </button>
        </div>
    </div>
    <div id="log" class="tab-pane fade in">
        <div class="col-md-12" style="padding-top: 15px;">
            <pre id="coretun_log_output" style="max-height: 500px; overflow-y: auto; font-size: 12px; background: #1e1e1e; color: #d4d4d4; padding: 10px;">{{ lang._('Loading...') }}</pre>
        </div>
    </div>
</div>

{{ partial('layout_partials/base_apply_button', {'data_endpoint': '/api/coretun/service/reconfigure', 'data_service_widget': 'coretun'}) }}
{{ partial("layout_partials/base_dialog",['fields':formDialogServer,'id':formGridServer['edit_dialog_id'],'label':lang._('Edit Server')])}}
{{ partial("layout_partials/base_dialog",['fields':formDialogSubscription,'id':formGridSubscription['edit_dialog_id'],'label':lang._('Edit Subscription')])}}
