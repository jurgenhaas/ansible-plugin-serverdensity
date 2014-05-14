import json
import os
import requests
import tempfile

from ansible.callbacks import vv
from ansible.errors import AnsibleError as ae
from ansible.runner.return_data import ReturnData
from ansible.utils import parse_kv

class ActionModule(object):
    ''' Create new host or sync all of your inventory over at ServerDensity'''

    ### We need to be able to modify the inventory
    BYPASS_HOST_LOOP = True
    TRANSFERS_FILES = False

    def __init__(self, runner):
        self.runner = runner
        self.api_token = None

        self.devices = []
        self.services = []
        self.alerts = []
        self.users = []
        self.notifications = []

        self.force_update = False
        self.cache_file_name = None

    def run(self, conn, tmp, module_name, module_args, inject, complex_args=None, **kwargs):

        if self.runner.noop_on_check(inject):
            return ReturnData(conn=conn, comm_ok=True, result=dict(skipped=True, msg='check mode not supported for this module'))

        args = {}
        if complex_args:
            args.update(complex_args)
        args.update(parse_kv(module_args))
        if not 'api_token' in args:
            raise ae("'api_token' is a required argument.")

        self.api_token = args.get('api_token')

        self.force_update = args.get('force', False)
        self.cache_file_name = args.get('cache', None)
        cleanup = args.get('cleanup', False)
        just_download = args.get('readonly', False)

        if just_download:
          self.force_update = False
          self.cache_file_name = tempfile.mktemp(prefix='sd_', suffix='.json')
          cleanup = False

        result = {}

        self.list_all()

        if just_download:
          vv('Downloaded settings to %s' % self.cache_file_name)
          return ReturnData(conn=conn, comm_ok=True, result=result)

        services = {}
        devicegroup_alerts = {}
        servicegroup_alerts = {}

        vv('Ensure hosts...')
        for host in self.runner.host_set:
            vv('- ' + host)
            host_vars = self.runner.inventory.get_variables(host)
            facts = host_vars.get('ansible_facts', {})
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
                    if not servicegroup_alerts.has_key(name):
                        host_servicegroup_alert = host_servicegroup_alerts.get(name)
                        servicegroup_alerts.__setitem__(name, host_servicegroup_alert)

            self.ensure_host(
                cpuCores=facts.get('ansible_processor_count', None),
                group=host_group,
                hostname=host,
                installedRAM=facts.get('ansible_memtotal_mb', None),
                name=host,
                os={
                    'code': facts.get('ansible_system', '')+' '+facts.get('ansible_distribution', '')+' '+facts.get('ansible_distribution_release', '')+' '+facts.get('ansible_distribution_version', ''),
                    'name': facts.get('ansible_system', ''),
                    },
                # privateIPs=facts[''],
                # privateDNS=facts[''],
                publicIPs=facts.get('ansible_all_ipv4_addresses', '')+facts.get('ansible_all_ipv6_addresses', ''),
                # publicDNS=facts[''],
                swapSpace=facts.get('ansible_swaptotal_mb', None),
                location={
                    'countryCode': location.get('countryCode'),
                    'countryName': location.get('countryName'),
                    'text': location.get('text'),
                    },
                provider=host_vars.get('provider')
            )

            alerts = host_vars.get('sd_alerts')
            if alerts:
                vv('- - Ensure device alerts...')
                for alertname in alerts:
                    vv('- - - ' + alertname)
                    alert = alerts.get(alertname)
                    self.ensure_alert(alert, 'device')

        vv('Ensure device group alerts...')
        for groupname in devicegroup_alerts:
            vv('- ' + groupname)
            group_alerts = devicegroup_alerts.get(groupname)
            for alertname in group_alerts:
                vv('- - ' + alertname)
                alert = group_alerts.get(alertname)
                self.ensure_alert(alert, 'deviceGroup', groupname)

        vv('Ensure services...')
        for servicename in services:
            vv('- ' + servicename)
            service = services.get(servicename)
            self.ensure_service(servicename, service)
            alerts = service.get('alerts')
            if alerts:
                vv('- - Ensure service alerts...')
                for alertname in alerts:
                    vv('- - - ' + alertname)
                    alert = alerts.get(alertname)
                    alert.__setitem__('service', service.get('name'))
                    self.ensure_alert(alert, 'service')

        vv('Ensure service group alerts...')
        for servicegroupname in servicegroup_alerts:
            vv('- ' + servicegroupname)
            alert = servicegroup_alerts.get(servicegroupname)
            groupname = alert.get('group')
            self.ensure_alert(alert, 'serviceGroup', groupname)

        if cleanup:
            vv('Cleanup unused alerts...')
            self.cleanup_alerts()

        vv('Completed successfully!')

        return ReturnData(conn=conn, comm_ok=True, result=result)

    def _request(self, path, data = None, method = 'GET'):
        encoder = json.JSONEncoder()
        postData = {}

        if data:
            method = 'POST'
            for key in data:
                item = data.get(key)
                if type(item) is list or type(item) is dict:
                    if len(item) > 0 or key == 'recipients':
                        item = encoder.encode(item)
                if type(item) is int or type(item) is unicode or type(item) is bool:
                    item = str(item)
                if item and type(item) is str and len(item) > 0:
                    postData.__setitem__(key, item)

        request_result = {}
        try:
            if method == 'GET':
                request_result = requests.get('https://api.serverdensity.io/' + path, params = {'token': self.api_token})
            elif method == 'POST':
                request_result = requests.post('https://api.serverdensity.io/' + path, params = {'token': self.api_token}, data = postData)
            elif method == 'DELETE':
                request_result = requests.delete('https://api.serverdensity.io/' + path, params = {'token': self.api_token})
        except ae, e:
            raise ae('No result from ServerDensity API')

        decoder = json.JSONDecoder()
        content = decoder.decode(request_result.content)
        if request_result.status_code != 200:
            msg = content['message']
            if content['errors']:
              for error in content['errors']:
                msg += ' // ' + error['description']
            raise ae('%s' % msg)
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
        found_alert = None
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

    def _list_objects(self, type, path):
        vv("Reading %s from SD" % type)
        changed = False
        allgroup = self.runner.inventory.get_group('all')
        allvariables = allgroup.get_variables()
        if not allvariables.has_key('_serverdensity_' + type):
            changed = True
            objects = self._request(path)
            allgroup.set_variable('_serverdensity_' + type, objects)
        else:
            objects = allvariables.get('_serverdensity_' + type)
        return changed, objects

    def _list_devices_agent_key(self):
        for device in self.devices:
            hostname = device.get('name')
            if hostname in self.runner.inventory._hosts_cache:
                host = self.runner.inventory._hosts_cache[hostname]
                host.set_variable('sd_agent_key', device.get('agentKey'))
            if hostname in self.runner.inventory._vars_per_host:
                hostvars = self.runner.inventory._vars_per_host.get(hostname)
                hostvars.__setitem__('sd_agent_key', device.get('agentKey'))

    def list_devices(self):
        if len(self.devices) == 0:
            (changed, self.devices) = self._list_objects('devices', 'inventory/devices')
            if changed:
                self._list_devices_agent_key()

    def list_services(self):
        if len(self.services) == 0:
            (changed, self.services) = self._list_objects('services', 'inventory/services')

    def list_alerts(self):
        if len(self.alerts) == 0:
            (changed, self.alerts) = self._list_objects('alerts', 'alerts/configs')

    def list_users(self):
        if len(self.users) == 0:
            (changed, self.users) = self._list_objects('users', 'users/users')

    def list_notifications(self):
        if len(self.notifications) == 0:
            filename = self.runner.inventory.basedir() + '/sd_notifications.json'
            if os.path.exists(filename):
                with open(filename, 'r') as content_file:
                    self.notifications = json.load(content_file)

    def list_all(self):
        if self.cache_file_name and os.path.exists(self.cache_file_name):
            with open(self.cache_file_name, 'r') as cache_file:
                cache = json.load(cache_file)
                self.devices = cache.get('devices', {})
                self._list_devices_agent_key()
                self.services = cache.get('services', {})
                self.alerts = cache.get('alerts', {})
                self.users = cache.get('users', {})
                self.notifications = cache.get('notifications', {})
                return
        self.list_devices()
        self.list_services()
        self.list_alerts()
        self.list_users()
        self.list_notifications()
        self.cache_update(True)

    def cache_update(self, force):
        if self.cache_file_name:
            if force or os.path.exists(self.cache_file_name):
                with open(self.cache_file_name, 'w') as cache_file:
                    cache = {
                        'devices': self.devices,
                        'services': self.services,
                        'alerts': self.alerts,
                        'users': self.users,
                        'notifications': self.notifications,
                        }
                    json.dump(cache, cache_file)

    def cache_reset(self):
        if self.cache_file_name and os.path.exists(self.cache_file_name):
            os.remove(self.cache_file_name)

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
            self.cache_reset()
        else:
            if not self.force_update:
                return
            path = 'inventory/devices/' + deviceId

        device = self._request(path, data)

        if deviceId:
            for old_device in self.devices:
                if old_device.get('hostname') == hostname:
                    self.devices.remove(old_device)
        self.devices.append(device)
        self.cache_update(False)

        if hostname in self.runner.inventory._hosts_cache:
            host = self.runner.inventory._hosts_cache[hostname]
            host.set_variable('sd_agent_key', device.get('agentKey'))
        if hostname in self.runner.inventory._vars_per_host:
            hostvars = self.runner.inventory._vars_per_host.get(hostname)
            hostvars.__setitem__('sd_agent_key', device.get('agentKey'))

    def ensure_service(self, servicename, service):
        serviceId = self._get_service_id(servicename)
        if not serviceId:
            path = 'inventory/services'
            self.cache_reset()
        else:
            if not self.force_update:
                return
            path = 'inventory/services/' + serviceId

        service = self._request(path, service)

        if not serviceId:
            self.services.append(service)

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
                    n = {
                        'type': n_type,
                        'id': id,
                        }
                    if actions:
                        n.__setitem__('actions', actions)
                    recipients.append(n)

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
            self.cache_reset()
        else:
            if not self.force_update:
                return
            path = 'alerts/configs/' + alertId

        if alertId:
            config.__setitem__('_id', alertId)
        self._request(path, config)

    def cleanup_alerts(self):
        for alert in self.alerts:
            updated = alert.get('ansible_updated')
            if not updated:
                self._request('alerts/configs/' + alert.get('_id'), method='DELETE')
                self.cache_reset()
