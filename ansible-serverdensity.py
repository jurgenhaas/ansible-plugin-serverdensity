#!/usr/bin/python
# coding=utf-8
__author__ = 'jurgenhaas'

import sys
import os
import json
import requests
from ansible.runner import Runner
import ansible.constants as C
from ansible import utils
from ansible import errors
from ansible import callbacks
from ansible import inventory

######################################################################
class Ansible(object):
    ''' code behind bin/ansible '''

    # ----------------------------------------------

    def __init__(self):
        self.stats = callbacks.AggregateStats()
        self.callbacks = callbacks.CliRunnerCallbacks()

    # ----------------------------------------------

    def parse(self):
        ''' create an options parser for bin/ansible '''

        parser = utils.base_parser(
            constants=C,
            runas_opts=True,
            subset_opts=True,
            async_opts=False,
            output_opts=True,
            connect_opts=True,
            check_opts=False,
            diff_opts=False,
            usage='%prog <host-pattern> [options]'
        )

        parser.add_option('-A', '--api-token', dest='api_token',
                          help='API Token for your ServerDensity account')

        options, args = parser.parse_args()
        self.callbacks.options = options

        if len(args) == 0 or len(args) > 1:
            parser.print_help()
            sys.exit(1)

        # su and sudo command line arguments need to be mutually exclusive
        if (options.su or options.su_user or options.ask_su_pass) and \
                (options.sudo or options.sudo_user or options.ask_sudo_pass):
            parser.error("Sudo arguments ('--sudo', '--sudo-user', and '--ask-sudo-pass') "
                         "and su arguments ('-su', '--su-user', and '--ask-su-pass') are "
                         "mutually exclusive")

        if (options.ask_vault_pass and options.vault_password_file):
            parser.error("--ask-vault-pass and --vault-password-file are mutually exclusive")

        # Ensure the api_token
        if not options.api_token:
            self.inventory_manager = inventory.Inventory(options.inventory)
            # Workaround: get variables from first host as get_group_variables never returns anything
            hosts = self.inventory_manager.list_hosts()
            first_host = hosts.pop(0)
            group_vars = self.inventory_manager.get_variables(first_host)
            options.api_token = group_vars.get('sd_api_token')
            if not options.api_token:
                try:
                    options.api_token = os.environ['SD_API_TOKEN']
                except KeyError, e:
                    raise errors.AnsibleError('Unable to load %s' % e.message)

        return (options, args)

    def host_vars(self, hostname):
        return self.inventory_manager.get_variables(hostname)

    # ----------------------------------------------

    def run(self, options, args):
        ''' use Runner lib to do SSH things '''

        pattern = args[0]

        """
        inventory_manager = inventory.Inventory(options.inventory)
        if options.subset:
            inventory_manager.subset(options.subset)
        hosts = inventory_manager.list_hosts(pattern)
        if len(hosts) == 0:
            callbacks.display("No hosts matched")
            sys.exit(0)

        if options.listhosts:
            for host in hosts:
                callbacks.display('    %s' % host)
            sys.exit(0)

        if ((options.module_name == 'command' or options.module_name == 'shell')
                and not options.module_args):
            callbacks.display("No argument passed to %s module" % options.module_name, color='red', stderr=True)
            sys.exit(1)
        """

        sshpass = None
        sudopass = None
        su_pass = None
        vault_pass = None

        options.ask_pass = options.ask_pass or C.DEFAULT_ASK_PASS
        # Never ask for an SSH password when we run with local connection
        if options.connection == "local":
            options.ask_pass = False
        options.ask_sudo_pass = options.ask_sudo_pass or C.DEFAULT_ASK_SUDO_PASS
        options.ask_su_pass = options.ask_su_pass or C.DEFAULT_ASK_SU_PASS
        options.ask_vault_pass = options.ask_vault_pass or C.DEFAULT_ASK_VAULT_PASS

        (sshpass, sudopass, su_pass, vault_pass) = utils.ask_passwords(ask_pass=options.ask_pass, ask_sudo_pass=options.ask_sudo_pass, ask_su_pass=options.ask_su_pass, ask_vault_pass=options.ask_vault_pass)

        # read vault_pass from a file
        if options.vault_password_file:
            this_path = os.path.expanduser(options.vault_password_file)
            try:
                f = open(this_path, "rb")
                tmp_vault_pass=f.read()
                f.close()
            except (OSError, IOError), e:
                raise errors.AnsibleError("Could not read %s: %s" % (this_path, e))

            # get rid of newline chars
            tmp_vault_pass = tmp_vault_pass.strip()

            if not options.ask_vault_pass:
                vault_pass = tmp_vault_pass

        inventory_manager = inventory.Inventory(options.inventory)
        if options.subset:
            inventory_manager.subset(options.subset)
        hosts = inventory_manager.list_hosts(pattern)
        if len(hosts) == 0:
            callbacks.display("No hosts matched")
            sys.exit(0)

        if options.listhosts:
            for host in hosts:
                callbacks.display('    %s' % host)
            sys.exit(0)

        if ((options.module_name == 'command' or options.module_name == 'shell')
            and not options.module_args):
            callbacks.display("No argument passed to %s module" % options.module_name, color='red', stderr=True)
            sys.exit(1)


        if options.su_user or options.ask_su_pass:
            options.su = True
        elif options.sudo_user or options.ask_sudo_pass:
            options.sudo = True
        options.sudo_user = options.sudo_user or C.DEFAULT_SUDO_USER
        options.su_user = options.su_user or C.DEFAULT_SU_USER
        if options.tree:
            utils.prepare_writeable_dir(options.tree)

        runner = Runner(
            module_name=options.module_name,
            module_path=options.module_path,
            module_args=options.module_args,
            remote_user=options.remote_user,
            remote_pass=sshpass,
            inventory=inventory_manager,
            timeout=options.timeout,
            private_key_file=options.private_key_file,
            forks=options.forks,
            pattern=pattern,
            callbacks=self.callbacks,
            sudo=options.sudo,
            sudo_pass=sudopass,
            sudo_user=options.sudo_user,
            transport=options.connection,
            subset=options.subset,
            check=options.check,
            diff=options.check,
            su=options.su,
            su_pass=su_pass,
            su_user=options.su_user,
            vault_pass=vault_pass
        )

        if options.seconds:
            callbacks.display("background launch...\n\n", color='cyan')
            results, poller = runner.run_async(options.seconds)
            results = self.poll_while_needed(poller, options)
        else:
            results = runner.run()

        return (runner, results)

    # ----------------------------------------------

    def poll_while_needed(self, poller, options):
        ''' summarize results from Runner '''

        # BACKGROUND POLL LOGIC when -B and -P are specified
        if options.seconds and options.poll_interval > 0:
            poller.wait(options.seconds, options.poll_interval)

        return poller.results

######################################################################
class ServerDensity(object):
    """
    TBD.
    """

    def __init__(self, api_token):
        self.api_token = api_token
        self.devices = {}
        self.services = {}

    def _request(self, path, data = None, method = 'GET'):
        encoder = json.JSONEncoder()
        postData = {}

        if data:
            method = 'POST'
            for key in data:
                item = data.get(key)
                if type(item) is list or type(item) is dict:
                    if len(item) > 0:
                        item = encoder.encode(item)
                if type(item) is int or type(item) is unicode or type(item) is bool:
                    item = str(item)
                if item and type(item) is str and len(item) > 0:
                    postData.__setitem__(key, item)

        if method == 'GET':
            result = requests.get('https://api.serverdensity.io/' + path, params = {'token': self.api_token})
        elif method == 'POST':
            result = requests.post('https://api.serverdensity.io/' + path, params = {'token': self.api_token}, data = postData)
        elif method == 'DELETE':
            result = requests.delete('https://api.serverdensity.io/' + path, params = {'token': self.api_token})

        decoder = json.JSONDecoder()
        content = decoder.decode(result.content)
        if result.status_code != 200:
            raise errors.AnsibleError('%s' % content['message'])
        return content

    def _get_device_id(self, hostname):
        for host in self.devices:
            if host.get('hostname') == hostname:
                return host.get('_id')
        return False

    def _get_service_id(self, servicename):
        for service in self.services:
            if service.get('name') == servicename:
                return service.get('_id')
        return False

    def _get_user_id(self, loginname):
        for user in self.users:
            if user.get('login') == loginname:
                return user.get('_id')
        return False

    def _get_alert_id(self, subjectType, subjectId, section, field, comparison, value):
        candidates = []
        found_alert = False
        for alert in self.alerts:
            if not alert.get('ansible_updated'):
                if alert.get('subjectType') == subjectType and alert.get('subjectId') == subjectId and alert.get('section') == section and alert.get('field') == field:
                    candidates.append(alert)

        if len(candidates) == 1:
            found_alert = candidates.pop(0)
        elif len(candidates) > 1:
            level = -1
            check_level = 0
            for check_alert in candidates:
                if check_alert.get('comparison') == comparison:
                    check_level += 1
                if check_alert.get('value') == value:
                    check_level += 2

                if check_level > level:
                    level = check_level
                    found_alert = check_alert

        if found_alert:
            found_alert.__setitem__('ansible_updated', True)
            return found_alert.get('_id')

        return False

    def _get_notification_id(self, type, name):
        for notification in self.notifications:
            if notification.get('type') == type and notification.get('name') == name:
                return notification.get('_id')
        return False

    def status(self):
        self.devices = self._request('inventory/devices')
        self.services = self._request('inventory/services')
        self.alerts = self._request('alerts/configs')
        self.users = self._request('users/users')
        # TODO: Remove the following workaround if we can get the notifications from the API
        with open(os.path.abspath(os.path.dirname(__file__) + '/notifications.json'), 'r') as content_file:
            self.notifications = json.load(content_file)

    def ensure_host(self, hostname, cpuCores=None, group=None, installedRAM=None,
                    name=None, os=None, privateIPs=None, privateDNS=None,
                    publicIPs=None, publicDNS=None, swapSpace=None, location=None,
                    provider=None):
        data = {
            'hostname': hostname,
            'cpuCores': cpuCores,
            'group': group,
            'installedRAM': installedRAM,
            'name': name or hostname,
            'os': os,
            'privateIPs': privateIPs,
            'privateDNS': privateDNS,
            'publicIPs': publicIPs,
            'publicDNS': publicDNS,
            'swapSpace': swapSpace,
            'location': location,
            'provider': provider,
        }
        deviceId = self._get_device_id(hostname)
        if not deviceId:
            path = 'inventory/devices'
        else:
            path = 'inventory/devices/' + deviceId
        self._request(path, data)
        # TODO: write back the HOST to self.devices if this is a new host

    def ensure_service(self, servicename, service):
        serviceId = self._get_service_id(servicename)
        if not serviceId:
            path = 'inventory/services'
        else:
            path = 'inventory/services/' + serviceId
        self._request(path, service)
        # TODO: write back the SERVICE to self.services if this is a new service

    def ensure_alert(self, alert, a_type, group=None):
        recipients = []
        notify = alert.get('notify')
        if notify:
            for item in notify:
                n_type = item['type']
                n_name = item['name']
                if n_type == 'user':
                    id = self._get_user_id(n_name)
                    actions = item['actions']
                else:
                    id = self._get_notification_id(n_type, n_name)
                    actions = None
                if id:
                    recipients.append({
                        'type': n_type,
                        'id': id,
                        'actions': actions,
                    })

        config = alert.get('config')
        config.__setitem__('group', group)
        if a_type == 'device':
            config.__setitem__('subjectId', self._get_device_id(alert.get('host')))
        elif a_type == 'service':
            config.__setitem__('subjectId', self._get_service_id(alert.get('service')))
        else:
            config.__setitem__('subjectId', group)
        config.__setitem__('subjectType', a_type)
        config.__setitem__('recipients', recipients)

        alertId = self._get_alert_id(config.get('subjectType'), config.get('subjectId'),
                                     config.get('section'), config.get('field'),
                                     config.get('comparison'), config.get('value'))
        if not alertId or len(alertId) == 0:
            path = 'alerts/configs'
        else:
            path = 'alerts/configs/' + alertId

        config.__setitem__('_id', alertId)
        self._request(path, config)

    def cleanup_alerts(self):
        for alert in self.alerts:
            updated = alert.get('ansible_updated')
            if not updated:
                self._request('alerts/configs/' + alert.get('_id'), method='DELETE')

########################################################
if __name__ == '__main__':
    C.DEFAULT_HASH_BEHAVIOUR = 'merge'

    callbacks.display("", log_only=True)
    callbacks.display(" ".join(sys.argv), log_only=True)
    callbacks.display("", log_only=True)

    ansible = Ansible()
    (options, args) = ansible.parse()
    options.module_name = 'setup'
    options.module_args = ''
    options.check = False
    options.seconds = 0

    callbacks.display('Collecting facts about the inventory...')
    try:
        (runner, results) = ansible.run(options, args)
        for result in results['contacted'].values():
            if 'failed' in result or result.get('rc', 0) != 0:
                sys.exit(2)
        if results['dark']:
            sys.exit(3)
    except errors.AnsibleError, e:
        # Generic handler for ansible specific errors
        callbacks.display("ERROR: %s" % str(e), stderr=True, color='red')
        sys.exit(1)

    callbacks.display('Initializing connection to ServerDensity...')
    try:
        sd_api = ServerDensity(options.api_token)
        sd_api.status()
    except Exception, e:
        raise errors.AnsibleError('%s' % e.msg)

    services = {}
    devicegroup_alerts = {}
    servicegroup_alerts = {}

    callbacks.display('Ensure hosts...')
    for host in results['contacted']:
        callbacks.display('  - ' + host)
        facts = results['contacted'][host]['ansible_facts']
        host_vars = ansible.host_vars(host)
        location = host_vars.get('location')
        if not location:
            location = {}

        host_services = host_vars.get('sd_services')
        if host_services:
            for host_service in host_services:
                name = host_service.get('name')
                if not services.has_key(name):
                    services.__setitem__(name, host_service)

        host_group = host_vars.get('sd_group')
        if not host_group:
            host_group = 'All others'

        host_devicegroup_alerts = host_vars.get('sd_devicegroup_alerts')
        if host_devicegroup_alerts:
            for name in host_devicegroup_alerts:
                host_devicegroup_alert = host_devicegroup_alerts.get(name)
                if not devicegroup_alerts.has_key(host_group):
                    devicegroup_alerts.__setitem__(host_group, {})
                alerts = devicegroup_alerts.get(host_group)
                if not alerts.has_key(name):
                    alerts.__setitem__(name, host_devicegroup_alert)
                    devicegroup_alerts.__setitem__(host_group, alerts)

        host_servicegroup_alerts = host_vars.get('sd_servicegroup_alerts')
        if host_servicegroup_alerts:
            for name in host_servicegroup_alerts:
                host_servicegroup_alert = host_servicegroup_alerts.get(name)
                if not servicegroup_alerts.has_key(host_group):
                    servicegroup_alerts.__setitem__(host_group, {})
                alerts = servicegroup_alerts.get(host_group)
                if not alerts.has_key(name):
                    alerts.__setitem__(name, host_servicegroup_alert)
                    servicegroup_alerts.__setitem__(host_group, alerts)

        sd_api.ensure_host(
            cpuCores=facts['ansible_processor_count'],
            group=host_group,
            hostname=host,
            installedRAM=facts['ansible_memtotal_mb'],
            name=host,
            os={
                'code': facts['ansible_system']+' '+facts['ansible_distribution']+' '+facts['ansible_distribution_release']+' '+facts['ansible_distribution_version'],
                'name': facts['ansible_system'],
                },
            # privateIPs=facts[''],
            # privateDNS=facts[''],
            publicIPs=facts['ansible_all_ipv4_addresses']+facts['ansible_all_ipv6_addresses'],
            # publicDNS=facts[''],
            swapSpace=facts['ansible_swaptotal_mb'],
            location={
                'countryCode': location.get('countryCode'),
                'countryName': location.get('countryName'),
                'text': location.get('text'),
            },
            provider=host_vars.get('provider')
        )

        alerts = host_vars.get('sd_alerts')
        if alerts:
            callbacks.display('    Ensure device alerts...')
            for alertname in alerts:
                callbacks.display('      - ' + alertname)
                alert = alerts.get(alertname)
                sd_api.ensure_alert(alert, 'device')

    callbacks.display('Ensure device group alerts...')
    for groupname in devicegroup_alerts:
        callbacks.display('  - ' + groupname)
        group_alerts = devicegroup_alerts.get(groupname)
        for alertname in group_alerts:
            callbacks.display('    - ' + alertname)
            alert = group_alerts.get(alertname)
            sd_api.ensure_alert(alert, 'deviceGroup', groupname)

    callbacks.display('Ensure services...')
    for servicename in services:
        callbacks.display('  - ' + servicename)
        service = services.get(servicename)
        sd_api.ensure_service(servicename, service)
        alerts = service.get('alerts')
        if alerts:
            callbacks.display('    Ensure service alerts...')
            for alertname in alerts:
                callbacks.display('      - ' + alertname)
                alert = alerts.get(alertname)
                sd_api.ensure_alert(alert, 'service')

    callbacks.display('Ensure service group alerts...')
    for groupname in servicegroup_alerts:
        callbacks.display('  - ' + groupname)
        group_alerts = servicegroup_alerts.get(groupname)
        for alertname in group_alerts:
            callbacks.display('    - ' + alertname)
            alert = group_alerts.get(alertname)
            sd_api.ensure_alert(alert, 'serviceGroup', groupname)

    callbacks.display('Cleanup unused alerts...')
    sd_api.cleanup_alerts()

    callbacks.display('Completed successfully!')
    sys.exit(0)
