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

    def _request(self, path, data = None):
        encoder = json.JSONEncoder()
        if data:
            postData = {}
            for key in data:
                item = data.get(key)
                if type(item) is list or type(item) is dict:
                    if len(item) > 0:
                        item = encoder.encode(item)
                if type(item) is int or type(item) is unicode:
                    item = str(item)
                if item and type(item) is str and len(item) > 0:
                    postData.__setitem__(key, item)
            result = requests.post('https://api.serverdensity.io/' + path, params = {'token': self.api_token}, data = postData)
        else:
            result = requests.get('https://api.serverdensity.io/' + path, params = {'token': self.api_token})
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

    def status(self):
        self.devices = self._request('inventory/devices')
        self.services = self._request('inventory/services')

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

########################################################
if __name__ == '__main__':
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

    callbacks.display('Ensure hosts and their data...')
    for host in results['contacted']:
        callbacks.display('  - ' + host)
        facts = results['contacted'][host]['ansible_facts']
        host_vars = ansible.host_vars(host)
        location = host_vars.get('location')
        if not location:
            location = {}

        sd_api.ensure_host(
            cpuCores=facts['ansible_processor_count'],
            group=host_vars.get('sd_group'),
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
