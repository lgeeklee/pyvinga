# !/usr/bin/env python

"""
Python program will import VMs and Datastores from an ESXi host and create an Icinga
hierarchy.
"""

#from __future__ import print_function
import argparse
import atexit
import getpass

from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vmodl, vim


vi_var_file = '/etc/icinga/objects/vi_commands.cfg'
domain_name = '.homelab.local'


def GetArgs():
    """
    Supports the command-line arguments listed below.
    """
    parser = argparse.ArgumentParser(description='Process args for retrieving all the Virtual Machines')
    parser.add_argument('-e', '--entity', required=True, action='store', help='Entity to setup (vCenter or ESXi host)')
    parser.add_argument('-o', '--port', type=int, default=443, action='store', help='Port to connect on')
    parser.add_argument('-u', '--user', required=True, action='store', help='User name to use when connecting to host')
    parser.add_argument('-p', '--password', required=False, action='store',
                        help='Password to use when connecting to host')
    args = parser.parse_args()
    return args


def GetProperties(content, viewType, props, specType):
    # Build a view and get basic properties for all Virtual Machines
    objView = content.viewManager.CreateContainerView(content.rootFolder, viewType, True)
    tSpec = vim.PropertyCollector.TraversalSpec(name='tSpecName', path='view', skip=False, type=vim.view.ContainerView)
    pSpec = vim.PropertyCollector.PropertySpec(all=False, pathSet=props, type=specType)
    oSpec = vim.PropertyCollector.ObjectSpec(obj=objView, selectSet=[tSpec], skip=False)
    pfSpec = vim.PropertyCollector.FilterSpec(objectSet=[oSpec], propSet=[pSpec], reportMissingObjectsInResults=False)
    retOptions = vim.PropertyCollector.RetrieveOptions()
    totalProps = []
    retProps = content.propertyCollector.RetrievePropertiesEx(specSet=[pfSpec], options=retOptions)
    totalProps += retProps.objects
    while retProps.token:
        retProps = content.propertyCollector.ContinueRetrievePropertiesEx(token=retProps.token)
        totalProps += retProps.objects
    objView.Destroy()
    # Turn the output in retProps into a usable dictionary of values
    gpOutput = []
    for eachProp in totalProps:
        propDic = {}
        for prop in eachProp.propSet:
            propDic[prop.name] = prop.val
        propDic['moref'] = eachProp.obj
        gpOutput.append(propDic)
    return gpOutput


def create_commands():
    f = open(vi_var_file, 'w')
    f.write('#\'check_pyvi\' command definition\n')
    f.write('define command {\n')
    f.write('\tcommand_name\tcheck_pyvi\n')
    f.write(
        '\tcommand_line\t/home/leeb/pyvinga/pyvinga.py -s $ARG1$ -u $USER01$ -p $USER02$ -n $ARG2$ -e \'$HOSTNAME$\' -r $ARG3$\n')
    f.write('\t}')
    f.close()


def create_config(entity, vmProps, dsProps):
    norm_entity = entity.split('.')[0]
    h_description = 'Virtual Machines'
    hostgroup_type = 'virtual-machines'

    hostgroup_name = create_hostgroup(hostgroup_type, entity, norm_entity, h_description)
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'CPU Ready', 'vm', 'cpu.ready')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Core Information', 'vm', 'core')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'CPU Usage', 'vm', 'cpu.usage')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Active', 'vm', 'mem.active')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Shared', 'vm', 'mem.shared')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Balloon', 'vm', 'mem.balloon')
    for vm in vmProps:
        create_host_vm(hostgroup_type, hostgroup_name, entity, vm['name'])

    h_description = 'Datastores'
    hostgroup_type = 'datastores'

    hostgroup_name = create_hostgroup(hostgroup_type, entity, norm_entity, h_description)
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Datastore Space', 'datastore', 'space')
    for ds in dsProps:
        create_host_ds(hostgroup_type, hostgroup_name, entity, ds['name'])

    h_description = 'Hosts'
    hostgroup_type = 'hosts'
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Core Information', 'host', 'core',
                   group=False)


def create_hostgroup(hostgroup_type, entity, norm_entity, h_description):
    vi_entity_file = '/etc/icinga/objects/vi_' + norm_entity + '_config.cfg'
    hostgroup_name = norm_entity + '-' + hostgroup_type

    f = open(vi_entity_file, 'a')
    f.write('#' + h_description + ' in hostgroup for this entity\n')
    f.write('#@' + entity + hostgroup_type + '\n')
    f.write('define hostgroup {\n')
    f.write('\thostgroup_name\t\t' + hostgroup_name + '\n')
    f.write('\talias\t\t\t' + norm_entity + ' ' + h_description + '\n')
    f.write('\t}\n\n')
    f.close()

    return hostgroup_name


def create_service(hostgroup_type, entity, norm_entity, s_use, s_description, counter_type, counter, group=True):
    vi_entity_file = '/etc/icinga/objects/vi_' + norm_entity + '_config.cfg'
    hostgroup_name = norm_entity + '-' + hostgroup_type

    f = open(vi_entity_file, 'a')
    f.write('#Service ' + s_description + ' for Virtual Machines\n')
    f.write('define service {\n')
    f.write('\tuse\t\t\tgeneric-service\n')
    if group:
        f.write('\thostgroup_name\t\t' + hostgroup_name + '\n')
    else:
        f.write('\thost_name\t\t' + norm_entity + '\n')
    f.write('\tservice_description\t' + s_description + '\n')
    f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!' + counter_type + '!' + counter + '\n')
    f.write('\t}\n\n')
    f.close()


def create_host_host(entity):
    norm_entity = entity.split('.')[0]
    vi_entity_file = '/etc/icinga/objects/vi_' + norm_entity + '_hosts.cfg'

    f = open(vi_entity_file, 'a')
    f.write('#Host ' + norm_entity + '\n')
    f.write('define host {\n')
    f.write('\tuse\t\t\tgeneric-host\n')
    f.write('\thost_name\t\t' + norm_entity + '\n')
    f.write('\talias\t\t\t' + norm_entity + '\n')
    f.write('\taddress\t\t\t' + entity + '\n')
    f.write('\t}\n\n')
    f.close()


def create_host_vm(hostgroup_type, hostgroup_name, entity, host_name):
    norm_entity = entity.split('.')[0]
    vi_entity_file = '/etc/icinga/objects/vi_' + norm_entity + '_hosts.cfg'

    f = open(vi_entity_file, 'a')
    f.write('#Stand Alone Host ' + norm_entity + '\n')
    f.write('define host {\n')
    f.write('\tuse\t\t\tgeneric-host\n')
    f.write('\thost_name\t\t' + host_name + '\n')
    f.write('\talias\t\t\t' + host_name + '\n')
    f.write('\taddress\t\t\t' + host_name + domain_name + '\n')
    f.write('\tparents\t\t\t' + norm_entity + '\n')
    f.write('\thostgroups\t\t' + hostgroup_name + '\n')
    f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!vm!status\n')
    f.write('\t}\n\n')
    f.close()


def create_host_ds(hostgroup_type, hostgroup_name, entity, host_name):
    norm_entity = entity.split('.')[0]
    vi_entity_file = '/etc/icinga/objects/vi_' + norm_entity + '_hosts.cfg'

    f = open(vi_entity_file, 'a')
    f.write('#Host ' + norm_entity + '\n')
    f.write('define host {\n')
    f.write('\tuse\t\t\tgeneric-host\n')
    f.write('\thost_name\t\t' + host_name + '\n')
    f.write('\talias\t\t\t' + host_name + ' Datastore\n')
    f.write('\tparents\t\t\t' + norm_entity + '\n')
    f.write('\thostgroups\t\t' + hostgroup_name + '\n')
    f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!datastore!status\n')
    f.write('\t}\n\n')
    f.close()


def get_vm_hierarchy(content):
    vmProps = GetProperties(content, [vim.VirtualMachine], ['name', 'runtime.host'], vim.VirtualMachine)
    hostProps = GetProperties(content, [vim.HostSystem], ['name', 'parent'], vim.HostSystem)
    for host in hostProps:
        if str(host['parent']).startswith('\'vim.Compute'):
            host['clustername'] = 'N/A - Stand Alone Host'
        elif str(host['parent']).startswith('\'vim.Cluster'):
            host['clustername'] = host['parent'].name

    for vm in vmProps:
        for host in hostProps:
            if vm['runtime.host'] == host['moref']:
                vm['host'] = host['name']
                vm['cluster'] = host['clustername']
        del vm['runtime.host']
        del vm['moref']

    return vmProps


def get_datastore_hierarchy(content):
    dsProps = GetProperties(content, [vim.Datastore], ['name'], vim.Datastore)
    return dsProps


def main():
    args = GetArgs()
    try:
        si = None
        if args.password:
            password = args.password
        else:
            password = getpass.getpass(prompt="Enter password for host {} and user {}: ".format(args.entity, args.user))
        try:
            si = SmartConnect(host=args.entity,
                              user=args.user,
                              pwd=password,
                              port=int(args.port))
        except IOError, e:
            pass
        if not si:
            print('Could not connect to the specified host using specified username and password')
            return -1

        atexit.register(Disconnect, si)
        content = si.RetrieveContent()

        vmProps = get_vm_hierarchy(content)
        dsProps = get_datastore_hierarchy(content)

        create_commands()
        create_config(args.entity, vmProps, dsProps)
        create_host_host(args.entity)  #creates the ESXi host

    except vmodl.MethodFault as e:
        print "Caught vmodl fault : " + e.msg
        return -1
    except Exception as e:
        print "Caught exception : " + str(e)
        return -1

    return 0


# Start program
if __name__ == "__main__":
    main()
