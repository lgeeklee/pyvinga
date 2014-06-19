# !/usr/bin/env python

"""
Python program will import VMs and Datastores from an ESXi host and create an Icinga
hierarchy.
"""

from __future__ import print_function
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
    """

    :param content:
    :param viewType:
    :param props:
    :param specType:
    :return:
    """
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
        '\tcommand_line\t/opt/pyvinga/pyvinga.py -s $ARG1$ -u $USER3$ -p $USER4$ -n $ARG2$ -e \'$HOSTNAME$\' -r $ARG3$\n')
    f.write('\t}')
    f.close()


def create_esxi_config(entity, vmProps, dsProps):
    """

    :param entity:
    :param vmProps:
    :param dsProps:
    """
    norm_entity = entity.split('.')[0]
    h_description = 'Virtual Machines'
    hostgroup_type = 'virtual-machines'

    create_esxi_host(entity)

    hostgroup_name = create_esxi_hostgroup(hostgroup_type, entity, norm_entity, h_description)
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'CPU Ready', 'vm', 'cpu.ready')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Core Information', 'vm', 'core')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'CPU Usage', 'vm', 'cpu.usage')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Active', 'vm', 'mem.active')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Shared', 'vm', 'mem.shared')
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Balloon', 'vm', 'mem.balloon')
    for vm in vmProps:
        create_esx_vm(hostgroup_type, hostgroup_name, entity, vm['name'])

    h_description = 'Datastores'
    hostgroup_type = 'datastores'

    hostgroup_name = create_esxi_hostgroup(hostgroup_type, entity, norm_entity, h_description)
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Datastore Space', 'datastore', 'space')
    for ds in dsProps:
        create_esxi_ds(hostgroup_type, hostgroup_name, entity, ds['name'])

    h_description = 'Hosts'
    hostgroup_type = 'hosts'
    create_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Core Information', 'host', 'core',
                   group=False)


def create_vcenter_config(entity, vm_props, dc_list, dc_sahost_list, dc_cl_list, cl_host_list, ds_table):
    """

    :param entity:
    :param vm_props:
    :param dc_list:
    :param dc_sahost_list:
    :param dc_cl_list:
    :param cl_host_list:
    :param ds_table:
    """
    for dc in dc_list:
        #Create Datacenter - Stand Alone Host host groups
        if dc_sahost_list:
            hostgroup_name = str(dc).lower() + '-hosts'
            hostgroup_type = 'Datacenter Hosts'
            create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type)
            for sahost in dc_sahost_list:
                if dc == sahost['dcname']:
                    host_type = 'sahost'
                    create_vc_host(dc, entity, sahost['hostname'], hostgroup_name, host_type)
        #Create Datastore hosts
        if ds_table:
            hostgroup_name = str(dc).lower() + '-datastores'
            hostgroup_type = 'Datacenter Datastores'
            create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type)
            for ds in ds_table:
                if dc == ds['dcname']:
                    host_type = 'datastore'
                    create_vc_host(dc, entity, ds['dsname'], hostgroup_name, host_type)
        #Create DataCenter - Cluster host groups
        if dc_cl_list:
            for dc_cl in dc_cl_list:
                if dc == dc_cl['dcname']:
                    hostgroup_name = str(dc).lower() + '-' + str(dc_cl['clustername']).lower() + '-hosts '
                    hostgroup_type = 'Datacenter Clusters'
                    create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type)
                    for cl_host in cl_host_list:
                        if dc_cl['clustername'] == cl_host['clustername']:
                            host_type = 'clhost'
                            create_vc_host(dc, entity, cl_host['hostname'], hostgroup_name, host_type, str(dc_cl['clustername']).lower())
                    pass
        #Create Stand Alone Host hosts
        for sahost in dc_sahost_list:
            if dc in sahost:
                #create_host_host(dc['hostname'])
                pass


def create_esxi_hostgroup(hostgroup_type, entity, norm_entity, h_description):
    """

    :param hostgroup_type:
    :param entity:
    :param norm_entity:
    :param h_description:
    :return:
    """
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


def create_esxi_host(entity):
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


def create_esx_vm(hostgroup_type, hostgroup_name, entity, host_name):
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


def create_esxi_ds(hostgroup_type, hostgroup_name, entity, host_name):
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


def create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type):
    """

    :param dc:
    :param entity:
    :param hostgroup_name:
    :param hostgroup_type:
    """
    vi_entity_file = 'C://Temp//vm_' + dc + '_config.cfg'

    f = open(vi_entity_file, 'a')
    f.write('#' + hostgroup_type + ' in hostgroup for this entity\n')
    f.write('#@' + entity + hostgroup_type + '\n')
    f.write('define hostgroup {\n')
    f.write('\thostgroup_name\t\t' + hostgroup_name + '\n')
    f.write('\talias\t\t\t' + dc + ' ' + hostgroup_type + '\n')
    f.write('\t}\n\n')
    f.close()


def create_vc_host(dc, entity, host_name, hostgroup_name, host_type, cl_name=''):
    vi_entity_file = 'C://Temp//vm_' + dc + '_hosts.cfg'
    norm_host = host_name.split('.')[0]

    f = open(vi_entity_file, 'a')
    f.write('#vCenter ' + entity + '\n')
    f.write('define host {\n')
    f.write('\tuse\t\t\tgeneric-host\n')
    f.write('\thost_name\t\t' + norm_host + '\n')
    f.write('\talias\t\t\t' + norm_host + ' ' + host_type + '\n')
    f.write('\taddress\t\t\t' + host_name + '\n')
    #if host_type == 'clhost':
    #    f.write('\tparents\t\t\t' + cl_name + '\n')
    if host_type != 'cluster':
        f.write('\thostgroups\t\t\t' + hostgroup_name + '\n')
    if host_type == 'datastore':
        f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!datastore!status\n')
    f.write('\t}\n\n')
    f.close()


def get_hierarchy(content):
    vm_props = GetProperties(content, [vim.VirtualMachine], ['name', 'runtime.host'], vim.VirtualMachine)
    host_props = GetProperties(content, [vim.HostSystem], ['name', 'parent'], vim.HostSystem)
    # Get the Datacenter to Stand Alone Host list
    dc_sahost_list = []
    # Get the Datacenter to Cluster list
    dc_cl_list = []
    # Get the Cluster to Host list
    cl_host_list = []
    for host in host_props:
        if str(host['parent']).startswith('\'vim.Compute'):
            host['clustername'] = False
            host['dcname'] = host['parent'].parent.parent.name
            sahost_list = {}
            sahost_list['hostname'] = host['name']
            sahost_list['dcname'] = host['dcname']
            if sahost_list in dc_sahost_list:
                pass
            else:
                dc_sahost_list.append(sahost_list)
        elif str(host['parent']).startswith('\'vim.Cluster'):
            host['clustername'] = host['parent'].name
            host['dcname'] = host['parent'].parent.parent.name
            cl_list = {}
            cl_list['clustername'] = host['parent'].name
            cl_list['dcname'] = host['dcname']
            if cl_list not in dc_cl_list:
                dc_cl_list.append(cl_list)
            clh_list = {}
            clh_list['clustername'] = host['parent'].name
            clh_list['hostname'] = host['name']
            if clh_list not in cl_host_list:
                cl_host_list.append(clh_list)

    for vm in vm_props:
        for host in host_props:
            if vm['runtime.host'] == host['moref']:
                vm['hostname'] = host['name']
                vm['clustername'] = host['clustername']
                vm['dcname'] = host['dcname']
        del vm['runtime.host']
        del vm['moref']

    #Get unique DC list
    dc_list = []
    for vm in vm_props:
        dc_list.append(vm['dcname'])
    dc_list = set(dc_list)

    return (vm_props, dc_list, dc_sahost_list, dc_cl_list, cl_host_list)


def get_datastore_hierarchy(content):
    dc_props = GetProperties(content, [vim.Datacenter], ['name', 'datastore'], vim.Datacenter)
    ds_table = []
    for datacenter in dc_props:
        for datastore in datacenter['datastore']:
            dc_dict = {}
            dc_dict['dcname'] = datacenter['name']
            dc_dict['dsname'] = datastore.name
            ds_table.append(dc_dict)
    return ds_table


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

        vm_props, dc_list, dc_sahost_list, dc_cl_list, cl_host_list = get_hierarchy(content)
        ds_table = get_datastore_hierarchy(content)

        create_vcenter_config(args.entity, vm_props, dc_list, dc_sahost_list, dc_cl_list, cl_host_list, ds_table)

        if content.about.name == 'VMware vCenter Server':
            print("vCenter detected")
            pass
        elif content.about.name == 'VMware ESXi':
            create_commands()
            create_esxi_config(args.entity, vm_props, ds_table)

    except vmodl.MethodFault as e:
        print("Caught vmodl fault : " + e.msg)
        return -1
    except Exception as e:
        print("Caught exception : " + str(e))
        return -1

    return 0


# Start program
if __name__ == "__main__":
    main()
