#!/usr/bin/env python

"""
Python program will import VMs and Datastores from an ESXi host or vCenter instance
and create an Icinga hierarchy.
"""

from __future__ import print_function
import argparse
import atexit
import getpass

from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vmodl, vim


# The path and name of the file where you want the custom command(s) to be stored
vi_var_file = '/etc/icinga/objects/vi_commands.cfg'
# Domain name of your ESXi hosts - only used when querying individual ESXi hosts
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


def get_properties(content, viewType, props, specType):
    # Build a view and get basic properties for all Virtual Machines
    """
    Obtains a list of specific properties for a particular Managed Object Reference data object.

    :param content: ServiceInstance Managed Object
    :param viewType: Type of Managed Object Reference that should populate the View
    :param props: A list of properties that should be retrieved for the entity
    :param specType: Type of Managed Object Reference that should be used for the Property Specification
    :return:
    """
    # Get the View based on the viewType
    objView = content.viewManager.CreateContainerView(content.rootFolder, viewType, True)
    # Build the Filter Specification
    tSpec = vim.PropertyCollector.TraversalSpec(name='tSpecName', path='view', skip=False, type=vim.view.ContainerView)
    pSpec = vim.PropertyCollector.PropertySpec(all=False, pathSet=props, type=specType)
    oSpec = vim.PropertyCollector.ObjectSpec(obj=objView, selectSet=[tSpec], skip=False)
    pfSpec = vim.PropertyCollector.FilterSpec(objectSet=[oSpec], propSet=[pSpec], reportMissingObjectsInResults=False)
    retOptions = vim.PropertyCollector.RetrieveOptions()
    # Retrieve the properties and look for a token coming back with each RetrievePropertiesEx call
    # If the token is present it indicates there are more items to be returned.
    totalProps = []
    retProps = content.propertyCollector.RetrievePropertiesEx(specSet=[pfSpec], options=retOptions)
    totalProps += retProps.objects
    while retProps.token:
        retProps = content.propertyCollector.ContinueRetrievePropertiesEx(token=retProps.token)
        totalProps += retProps.objects
    objView.Destroy()
    # Turn the output in totalProps into a usable dictionary of values
    gpOutput = []
    for eachProp in totalProps:
        propDic = {}
        for prop in eachProp.propSet:
            propDic[prop.name] = prop.val
        propDic['moref'] = eachProp.obj
        gpOutput.append(propDic)
    return gpOutput


def create_commands():
    """
    Create the file that will store the command definition to for check_pyvi.

    Ensure $USER3$ and $USER4$ are configured.  If multiple sets of credentials are stored then
    multiple commands would need to be entered into this file.
    """
    f = open(vi_var_file, 'w')
    f.write('#\'check_pyvi\' command definition\n')
    f.write('define command {\n')
    f.write('\tcommand_name\tcheck_pyvi\n')
    f.write(
        '\tcommand_line\t/opt/pyvinga/pyvinga.py -s $ARG1$ -u $USER3$ -p $USER4$ -n $ARG2$ -e \'$HOSTNAME$\' -r $ARG3$ -w $ARG4$ -c $ARG5$\n')
    f.write('\t}\n\n')
    f.close()


def create_esxi_config(entity, vmProps, dsProps):
    """
    Create the the configuration for an ESXi host

    :param entity: The ESXi host passed on the command line
    :param vmProps: The Virtual Machine hierarchy details for this ESXi host
    :param dsProps: The Datastore hierarchy details for this ESXi host
    """
    norm_entity = entity.split('.')[0]
    h_description = 'Virtual Machines'
    hostgroup_type = 'virtual-machines'

    create_esxi_host(entity)

    hostgroup_name = create_esxi_hostgroup(hostgroup_type, entity, norm_entity, h_description)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'CPU Ready', 'vm', 'cpu.ready', 5, 10)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Core Information', 'vm', 'core', 0, 0)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'CPU Usage', 'vm', 'cpu.usage', 50, 90)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Active', 'vm', 'mem.active', 80, 90)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Shared', 'vm', 'mem.shared', 98, 99)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Memory Balloon', 'vm', 'mem.balloon', 50, 75)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Datastore IO', 'vm', 'datastore.io', 250, 500)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Datastore Latency', 'vm', 'datastore.latency', 10, 20)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Network Usage', 'vm', 'network.usage', 10, 100)
    for vm in vmProps:
        create_esxi_vm(hostgroup_type, hostgroup_name, entity, vm['name'], 0, 0)

    h_description = 'Datastores'
    hostgroup_type = 'datastores'

    hostgroup_name = create_esxi_hostgroup(hostgroup_type, entity, norm_entity, h_description)
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Datastore Space', 'datastore', 'space', 50, 60)
    for ds in dsProps:
        create_esxi_ds(hostgroup_type, hostgroup_name, entity, ds['name'], 0, 0)

    h_description = 'Hosts'
    hostgroup_type = 'hosts'
    create_esxi_service(hostgroup_type, entity, norm_entity, 'generic-service', 'Core Information', 'host', 'core',
                   0, 0, group=False)


def create_esxi_hostgroup(hostgroup_type, entity, norm_entity, h_description):
    """
    Create the ESXi Host Group config

    :param hostgroup_type: Hyphen separated friendly name for host group type
    :param entity: The ESXi host passed on the command line
    :param norm_entity: The ESXi host name passed from command line with the FQDN suffix
    :param h_description: Friendly description for host group type
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


def create_esxi_service(hostgroup_type, entity, norm_entity, service_template, s_description, counter_type, counter, warning, critical, group=True):
    """
    Generates the service definitions for VMs, Host and Datastore on an ESXi host

    :param hostgroup_type: Hyphen separated friendly name for host group type
    :param entity: The ESXi host passed on the command line
    :param norm_entity: The ESXi host name passed from command line with the FQDN suffix
    :param service_template:
    :param s_description: Friendly description for the service
    :param counter_type: Type of counter being supplied (e.g. vm, host, datastore)
    :param counter: Friendly name of the counter
    :param warning: The warning value for the counter supplied by the command definition
    :param critical: The critical value for the counter supplied by the command definition
    :param group: Whether the service should be assigned to a host group
    """
    vi_entity_file = '/etc/icinga/objects/vi_' + norm_entity + '_config.cfg'
    hostgroup_name = norm_entity + '-' + hostgroup_type

    f = open(vi_entity_file, 'a')
    f.write('#Service ' + s_description + ' for Virtual Machines\n')
    f.write('define service {\n')
    f.write('\tuse\t\t\t + service_template + \n')
    if group:
        f.write('\thostgroup_name\t\t' + hostgroup_name + '\n')
    else:
        f.write('\thost_name\t\t' + norm_entity + '\n')
    f.write('\tservice_description\t' + s_description + '\n')
    f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!' + counter_type + '!' + counter + '!' + str(warning) + '!' + str(critical) + '\n')
    f.write('\t}\n\n')
    f.close()


def create_esxi_host(entity):
    """
    Generates the ESXi host definition for a stand alone ESXi host connection

    :param entity: The ESXi host passed on the command line
    """
    norm_entity = entity.split('.')[0]
    vi_entity_file = '/etc/icinga/objects/vi_' + norm_entity + '_hosts.cfg'

    f = open(vi_entity_file, 'a')
    f.write('#Host ' + norm_entity + '\n')
    f.write('define host {\n')
    f.write('\tuse\t\t\tgeneric-host\n')
    f.write('\thost_name\t\t' + entity + '\n')
    f.write('\talias\t\t\t' + norm_entity + '\n')
    f.write('\taddress\t\t\t' + entity + '\n')
    f.write('\t}\n\n')
    f.close()


def create_esxi_vm(hostgroup_type, hostgroup_name, entity, host_name, warning, critical):
    """
    Generates the individual Virtual Machine host definitions for a stand alone ESXi host connection

    :param hostgroup_type: Hyphen separated friendly name for host group type
    :param hostgroup_name: Name of the ESXi host group generated previously
    :param entity: The ESXi host passed on the command line
    :param host_name: The name of the Virtual Machine
    :param warning: The warning value for the counter supplied by the command definition
    :param critical: The critical value for the counter supplied by the command definition
    """
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
    f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!vm!status!' + str(warning) + '!' + str(critical) + '!' + '\n')
    f.write('\t}\n\n')
    f.close()


def create_esxi_ds(hostgroup_type, hostgroup_name, entity, host_name, warning, critical):
    """
    Generates the individual Datastore host definitions for a stand alone ESXi host connection

    :param hostgroup_type: Hyphen separated friendly name for host group type
    :param hostgroup_name: Name of the ESXi host group generated previously
    :param entity: The ESXi host passed on the command line
    :param host_name: The name of the Datastore
    :param warning: The warning value for the counter supplied by the command definition
    :param critical: The critical value for the counter supplied by the command definition
    """
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
    f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!datastore!status!' + str(warning) + '!' + str(critical) + '!' + '\n')
    f.write('\t}\n\n')
    f.close()


def create_vcenter_config(entity, vm_props, dc_list, dc_sahost_list, dc_cl_list, cl_host_list, ds_table):
    """
    Create the configuration for a vCenter instance

    :param entity: The vCenter instance passed on the command line
    :param vm_props: The Virtual Machine hierarchy details for this ESXi host
    :param dc_list: The unique list of vCenter Datacenters
    :param dc_sahost_list: A list of each vCenter Datacenter and its stand alone ESXi hosts
    :param dc_cl_list: A list of each vCenter Datacenter and its Clusters
    :param cl_host_list: A list of each vCenter Cluster and its ESXi hosts
    :param ds_table: The Datastore hierarchy details for this ESXi host
    """
    # Create variables to hold the unique list of hostgroups as they're created
    host_hglist = []
    ds_hglist = []
    cl_hglist = []
    vm_hglist = []

    # Check through each vCenter Datacenter and create the relevant configuration
    for dc in dc_list:
        if dc_sahost_list:
            # Create Datacenter - stand alone ESXi host groups
            hostgroup_name = str(dc).lower() + '-hosts'
            hostgroup_type = 'Datacenter Hosts'
            create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type)
            host_hglist.append(hostgroup_name)
            for sahost in dc_sahost_list:
                if dc == sahost['dcname']:
                    # Create stand alone alone ESXi host objects
                    host_type = 'sahost'
                    create_vc_host(dc, entity, sahost['hostname'], hostgroup_name, host_type, 0, 0)
                    # Create stand alone ESXi host = Virtual Machine host groups
                    hostgroup_name = str(dc).lower() + '-' + str(sahost['hostname']).split('.')[0] + '-vms'
                    hostgroup_type = 'Host VMs'
                    create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type, str(sahost['hostname']).split('.')[0])
                    vm_hglist.append(hostgroup_name)
        #Create Datastore hosts
        if ds_table:
            # Create Datacenter - Datastore host groups
            hostgroup_name = str(dc).lower() + '-datastores'
            hostgroup_type = 'Datacenter Datastores'
            create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type)
            ds_hglist.append(hostgroup_name)
            for ds in ds_table:
                if dc == ds['dcname']:
                    # Create Datacenter Datastore host objects
                    host_type = 'datastore'
                    create_vc_host(dc, entity, ds['dsname'], hostgroup_name, host_type, 0, 0)
        if dc_cl_list:
            for dc_cl in dc_cl_list:
                if dc == dc_cl['dcname']:
                    #Create Datacenter - Cluster host groups
                    hostgroup_name = str(dc).lower() + '-clusters '
                    hostgroup_type = 'Datacenter Clusters'
                    create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type)
                    # Create Cluster ESXi host objects
                    host_type = 'cluster'
                    create_vc_host(dc, entity, str(dc_cl['clustername']).lower(), hostgroup_name, host_type, 0, 0, dc_cl['clustername'])
                    cl_hglist.append(hostgroup_name)
                    # Create Cluster - Virtual MAchines host groups
                    hostgroup_name = str(dc).lower() + '-' + str(dc_cl['clustername']).lower() + '-vms '
                    hostgroup_type = 'Cluster VMs'
                    create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type, dc_cl['clustername'])
                    vm_hglist.append(hostgroup_name)
                    # Create Cluster - ESXi host groups
                    hostgroup_name = str(dc).lower() + '-' + str(dc_cl['clustername']).lower() + '-hosts '
                    hostgroup_type = 'Cluster Hosts'
                    create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type, dc_cl['clustername'])
                    host_hglist.append(hostgroup_name)
                    for cl_host in cl_host_list:
                        if dc_cl['clustername'] == cl_host['clustername']:
                            # Create Cluster ESXi host objects
                            host_type = 'clhost'
                            create_vc_host(dc, entity, cl_host['hostname'], hostgroup_name, host_type, 0, 0, dc_cl['clustername'])

    for vm in vm_props:
        # Create Virtual Machine host objects
        host_type = 'vm'
        if vm['clustername'] == False:
            vm_parent = str(vm['hostname']).split('.')[0]
        else:
            vm_parent = vm['clustername']
        create_vc_host(vm['dcname'], entity, vm['name'], vm['hostgroup_name'], host_type, 0, 0, vm_parent)

    #Create Virtual Machine services
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'CPU Ready', 'vm', 'cpu.ready', 5, 10)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'Core Information', 'vm', 'core', 0, 0)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'CPU Usage', 'vm', 'cpu.usage', 50, 90)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'Memory Active', 'vm', 'mem.active', 80, 90)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'Memory Shared', 'vm', 'mem.shared', 98, 99)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'Memory Balloon', 'vm', 'mem.balloon', 50, 60)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'Datastore IO', 'vm', 'datastore.io', 250, 500)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'Datastore Latency', 'vm', 'datastore.latency', 10, 20)
    create_vc_service(entity, ','.join(vm_hglist), 'generic-service', 'Network Usage', 'vm', 'network.usage', 10, 100)
    #Create Datastore services
    create_vc_service(entity, ','.join(ds_hglist), 'generic-service', 'Datastore Space', 'datastore', 'space', 75, 85)
    #Create Host services
    create_vc_service(entity, ','.join(host_hglist), 'generic-service', 'Core Information', 'host', 'core', 0, 0)


def create_vc_hostgroup(dc, entity, hostgroup_name, hostgroup_type, cl_name=''):
    """
    Add a hostgroup object to the file named after the vCenter Datacenter

    :param dc: The vCenter Datacenter name
    :param entity: The vCenter instance passed on the command line
    :param hostgroup_name: The name of the hostgroup
    :param hostgroup_type: A descriptive name for the hostgroup
    :param cl_name: Optional, but allows a Cluster name to be supplied to the function
    """
    vi_entity_file = '/etc/icinga/objects/vc_' + dc + '_config.cfg'

    f = open(vi_entity_file, 'a')
    f.write('#' + hostgroup_type + ' in hostgroup for ' + entity + '\n')
    f.write('define hostgroup {\n')
    f.write('\thostgroup_name\t\t' + hostgroup_name + '\n')
    f.write('\talias\t\t\t' + dc + ' ' + cl_name + ' ' + hostgroup_type + '\n')
    f.write('\t}\n\n')
    f.close()


def create_vc_host(dc, entity, host_name, hostgroup_name, host_type, warning, critical, cl_name=''):
    """
    Add a host object to the file named after the vCenter Datacenter

    :param dc: The vCenter Datacenter name
    :param entity: The vCenter instance passed on the command line
    :param host_name: The name of the actual host object to be monitored
    :param hostgroup_name: The name of the hostgroup
    :param host_type: A host type that helps with determining which lines to write to the .cfg file.
    This will typically be cluster, datastore, clhost, sahost, vm
    :param cl_name: Optional, but allows a Cluster name to be supplied to the function
    :param warning: The warning value for the counter supplied by the command definition
    :param critical: The critical value for the counter supplied by the command definition
    """
    vi_entity_file = '/etc/icinga/objects/vc_' + dc + '_hosts.cfg'
    norm_host = host_name.split('.')[0]

    f = open(vi_entity_file, 'a')
    f.write('define host {\n')
    f.write('\tuse\t\t\tgeneric-host\n')
    if host_type == 'cluster':
        f.write('\thost_name\t\t' + cl_name + '\n')
    elif host_type == 'clhost' or host_type == 'sahost':
        f.write('\thost_name\t\t' + host_name + '\n')
    else:
        f.write('\thost_name\t\t' + norm_host + '\n')
    f.write('\talias\t\t\t' + norm_host + ' ' + host_type + '\n')
    if host_type != 'cluster' and host_type != 'datastore':
        f.write('\taddress\t\t\t' + host_name + '\n')
    if host_type == 'clhost' or host_type == 'vm':
        f.write('\tparents\t\t\t' + cl_name + '\n')
    f.write('\thostgroups\t\t\t' + hostgroup_name + '\n')
    if host_type == 'datastore':
        f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!datastore!status!' + str(warning) + '!' + str(critical) + '\n')
    elif host_type == 'cluster':
        f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!cluster!status!' + str(warning) + '!' + str(critical) + '\n')
    elif host_type == 'vm':
            f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!vm!status!' + str(warning) + '!' + str(critical) + '\n')
    f.write('\t}\n\n')
    f.close()


def create_vc_service(entity, hostgroup_name, service_template, s_description, counter_type, counter, warning, critical):
    """
    Add a service object to the file named after the vCenter Datacenter

    :param entity: The vCenter instance passed on the command line
    :param hostgroup_name: One or more hostgroups supplied as a comma separated string
    :param service_template: The name of a template to use in the service
    :param s_description: A descriptive name for the service
    :param counter_type: The counter type - can be vm, datastore, host or cluster
    :param counter: The counter name, this should match the functions in pyvinga.py
    :param warning: The warning value for the counter supplied by the command definition
    :param critical: The critical value for the counter supplied by the command definition
    """
    vi_services_file = '/etc/icinga/objects/vc_services_config.cfg'

    f = open(vi_services_file, 'a')
    f.write('#Service ' + s_description + ' for ' + counter_type + '\n')
    f.write('define service {\n')
    f.write('\tuse\t\t\t' + service_template + '\n')
    f.write('\thostgroup_name\t\t' + hostgroup_name + '\n')
    f.write('\tservice_description\t' + s_description + '\n')
    f.write('\tcheck_command\t\tcheck_pyvi!' + entity + '!' + counter_type + '!' + counter + '!' + str(warning) + '!' + str(critical) + '\n')
    f.write('\t}\n\n')
    f.close()


def get_hierarchy(content):
    """
    Build the hierarchy of Clusters, Stand ALone Hosts, CLuster Hosts, DataCenters and Virtual Machines

    :param content: ServiceInstance Managed Object
    """
    vm_props = get_properties(content, [vim.VirtualMachine], ['name', 'runtime.host'], vim.VirtualMachine)
    host_props = get_properties(content, [vim.HostSystem], ['name', 'parent'], vim.HostSystem)
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
                if host['clustername'] == False:
                    vm['hostgroup_name'] = str(host['dcname']).lower() + '-' + str(host['name']).split('.')[0].lower() + '-vms'
                else:
                    vm['hostgroup_name'] = str(host['dcname']).lower() + '-' + str(host['clustername']).lower() + '-vms'
        del vm['runtime.host']

    #Get unique DC list
    dc_list = []
    for vm in vm_props:
        dc_list.append(vm['dcname'])
    dc_list = set(dc_list)



    return (vm_props, dc_list, dc_sahost_list, dc_cl_list, cl_host_list)


def get_datastore_hierarchy(content):
    """
    Get the Datastore hierarchy for each Datacenter

    :param content: ServiceInstance Managed Object
    """
    dc_props = get_properties(content, [vim.Datacenter], ['name', 'datastore'], vim.Datacenter)
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

        if content.about.name == 'VMware vCenter Server':
            print("vCenter Instance detected")
            create_commands()
            create_vcenter_config(args.entity, vm_props, dc_list, dc_sahost_list, dc_cl_list, cl_host_list, ds_table)
            pass
        elif content.about.name == 'VMware ESXi':
            print("ESXi Host detected")
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
