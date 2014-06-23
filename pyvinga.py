#!/usr/bin/env python

"""
Python program that will query requested counters in vCenter and return
status information for Icinga
"""

#from __future__ import print_function
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vmodl, vim
from datetime import timedelta, datetime
from os import path

import argparse
import atexit
import getpass


STATE_OK = 0
STATE_WARNING = 1
STATE_CRITICAL = 2
STATE_UNKNOWN = 3
state_tuple = 'OK', 'WARNING', 'CRITICAL', 'UNKNOWN'


def GetArgs():
    """
    Supports the command-line arguments listed below.
    """
    parser = argparse.ArgumentParser(description='Process args for retrieving all the Virtual Machines')
    parser.add_argument('-s', '--host', required=True, action='store', help='Remote host to connect to')
    parser.add_argument('-o', '--port', type=int, default=443, action='store', help='Port to connect on')
    parser.add_argument('-u', '--user', required=True, action='store', help='User name to use when connecting to host')
    parser.add_argument('-p', '--password', required=False, action='store',
                        help='Password to use when connecting to host')
    parser.add_argument('-n', '--type', required=True, action='store', help='values should be vm,host or datastore')
    parser.add_argument('-e', '--entity', required=True, action='store', help='One or more entities to report on')
    parser.add_argument('-r', '--counter', required=True, action='store', help='Performance Counter Name')
    parser.add_argument('-w', '--warning', required=False, action='store', help='Warning level for the counter')
    parser.add_argument('-c', '--critical', required=False, action='store', help='Critical level for the counter')
    args = parser.parse_args()
    return args


def BuildQuery(content, counterId, instance, vm):
    perfManager = content.perfManager
    metricId = vim.PerformanceManager.MetricId(counterId=counterId, instance=instance)
    startTime = datetime.now() - timedelta(seconds=60)
    endTime = datetime.now() - timedelta(seconds=40)
    query = vim.PerformanceManager.QuerySpec(intervalId=20, entity=vm, metricId=[metricId], startTime=startTime,
                                             endTime=endTime)
    perfResults = perfManager.QueryPerf(querySpec=[query])
    statData = float(sum(perfResults[0].value[0].value))
    return statData


def vmStatus(vmObj):
    finalOutput = str(vmObj.overallStatus)
    extraOutput = '(State: ' + vmObj.summary.runtime.powerState + ')'
    PrintOutputString(finalOutput, 'Virtual Machine Status', 'yellow', 'red', 'gray', extraOutput)


def vmCore(vmObj):
    vmconfig = vmObj.summary.config
    if (float(vmconfig.memorySizeMB) / 1024).is_integer():
        vm_memory = str(vmconfig.memorySizeMB / 1024) + ' GB'
    else:
        vm_memory = str(vmconfig.memorySizeMB) + ' MB'
    print "{}, {}, {} vCPU(s), {} Memory".format(vmconfig.annotation,
                                                        vmconfig.guestFullName, vmObj.summary.config.numCpu,
                                                        (vm_memory))
    exit(STATE_OK)


def hostCore(hostObj):
    hosthardware = hostObj.summary.hardware
    print "{}, {} x {} CPU(s) ({} Cores, {} Logical), {:.0f} GB Memory".format(hosthardware.model,
                                                                               hosthardware.numCpuPkgs,
                                                                               hosthardware.cpuModel,
                                                                               hosthardware.numCpuCores,
                                                                               hosthardware.numCpuThreads,
                                                                               (hosthardware.memorySize / 1024 / 1024 / 1024))
    exit(STATE_OK)


def clusterStatus(clusterObj):
    finalOutput = str(clusterObj.overallStatus)
    PrintOutputString(finalOutput, 'Cluster Status', 'yellow', 'red', 'gray')


def CpuReady(vmObj, content, perf_dict, warning, critical):
    counter_key = StatCheck(perf_dict, 'cpu.ready.summation')
    statData = BuildQuery(content, counter_key, "", vmObj)
    finalOutput = (statData / 20000 * 100)
    PrintOutputFloat(finalOutput, 'CPU Ready', warning, critical, '%')


def CpuUsage(vmObj, content, perf_dict, warning, critical):
    counter_key = StatCheck(perf_dict, 'cpu.usage.average')
    statData = BuildQuery(content, counter_key, "", vmObj)
    finalOutput = (statData / 100)
    PrintOutputFloat(finalOutput, 'CPU Usage', warning, critical, '%')


def MemoryActive(vmObj, content, perf_dict, warning, critical):
    counter_key = StatCheck(perf_dict, 'mem.active.average')
    statData = BuildQuery(content, counter_key, "", vmObj)
    finalOutput = (statData / 1024)
    PrintOutputFloat(finalOutput, 'Memory Active', (warning * vmObj.summary.config.memorySizeMB / 100),
                     (critical * vmObj.summary.config.memorySizeMB / 100), 'MB')


def MemoryShared(vmObj, content, perf_dict, warning, critical):
    counter_key = StatCheck(perf_dict, 'mem.shared.average')
    statData = BuildQuery(content, counter_key, "", vmObj)
    finalOutput = (statData / 1024)
    PrintOutputFloat(finalOutput, 'Memory Shared', (warning * vmObj.summary.config.memorySizeMB / 100),
                     (critical * vmObj.summary.config.memorySizeMB / 100), 'MB')


def MemoryBalloon(vmObj, content, perf_dict, warning, critical):
    counter_key = StatCheck(perf_dict, 'mem.vmmemctl.average')
    statData = BuildQuery(content, counter_key, "", vmObj)
    finalOutput = (statData / 1024)
    PrintOutputFloat(finalOutput, 'Memory Balloon', (warning * vmObj.summary.config.memorySizeMB / 100),
                     (critical * vmObj.summary.config.memorySizeMB / 100), 'MB')


def DataStoreIORead(vmObj, content, perf_dict, warning, critical):
    counter_key = StatCheck(perf_dict, 'datastore.numberReadAveraged.average')
    statData = BuildQuery(content, counter_key, "*", vmObj)
    PrintOutputFloat(statData, 'Datastore Read IOPS', warning, critical, 'IOPS')


def DataStoreIOWrite(vmObj, content, perf_dict, warning, critical):
    counter_key = StatCheck(perf_dict, 'datastore.numberWriteAveraged.average')
    statData = BuildQuery(content, counter_key, "*", vmObj)
    PrintOutputFloat(statData, 'Datastore Read IOPS', warning, critical, 'IOPS')


def datastoreSpace(datastoreObj, warning, critical):
    datastoreCapacity = float(datastoreObj.summary.capacity / 1024 / 1024 / 1024)
    datastoreFree = float(datastoreObj.summary.freeSpace / 1024 / 1024 / 1024)
    datastoreUsedPct = ((1 - (datastoreFree / datastoreCapacity)) * 100)
    extraOutput = "(Used {:.1f} GB of {:.1f} GB)".format((datastoreUsedPct * datastoreCapacity / 100),
                                                         datastoreCapacity)
    PrintOutputFloat(datastoreUsedPct, 'Datastore Used Space', warning, critical, '%', extraOutput)


def datastoreStatus(datastoreObj):
    finalOutput = str(datastoreObj.overallStatus)
    extraOutput = '(Type: ' + datastoreObj.summary.type + ')'
    PrintOutputString(finalOutput, 'Datastore Status', 'yellow', 'red', 'gray', extraOutput)


def StatCheck(perf_dict, counter_name):
    counter_key = perf_dict[counter_name]
    return counter_key


def GetProperties(content, viewType, props, specType):
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


def PrintOutputFloat(finalOutput, statName, warnValue, critValue, suffix, extraOutput=''):
    if finalOutput >= critValue:
        print "{} - {} is {:.1f} {} {}".format(state_tuple[STATE_CRITICAL], statName, finalOutput, suffix, extraOutput)
        exit(STATE_CRITICAL)
    elif finalOutput >= warnValue:
        print "{} - {} is {:.1f} {} {}".format(state_tuple[STATE_WARNING], statName, finalOutput, suffix, extraOutput)
        exit(STATE_WARNING)
    else:
        print "{} - {} is {:.1f} {} {}".format(state_tuple[STATE_OK], statName, finalOutput, suffix, extraOutput)
        exit(STATE_OK)


def PrintOutputString(finalOutput, statName, warnValue, critValue, unkValue, extraOutput=''):
    if finalOutput == critValue:
        print "{} - {} is {} {}".format(state_tuple[STATE_CRITICAL], statName, finalOutput, extraOutput)
        exit(STATE_CRITICAL)
    elif finalOutput == warnValue:
        print "{} - {} is {} {}".format(state_tuple[STATE_WARNING], statName, finalOutput, extraOutput)
        exit(STATE_WARNING)
    elif finalOutput == unkValue:
        print "{} - {} is {} {}".format(state_tuple[STATE_UNKNOWN], statName, finalOutput, extraOutput)
        exit(STATE_WARNING)
    else:
        print "{} - {} is {} {}".format(state_tuple[STATE_OK], statName, finalOutput, extraOutput)
        exit(STATE_OK)


def write_perf_dictionary(content, file_perf_dic):
    if not path.exists(file_perf_dic) or datetime.fromtimestamp(path.getmtime(file_perf_dic)) < (datetime.now() - timedelta(days=7)):
        # Get all the vCenter performance counters
        perf_dict = {}
        perfList = content.perfManager.perfCounter
        f = open(file_perf_dic, mode='w')
        for counter in perfList:
            counter_full = "{}.{}.{}".format(counter.groupInfo.key, counter.nameInfo.key, counter.rollupType)
            perf_dict[counter_full] = counter.key
            f.write(counter_full + ',' + str(perf_dict[counter_full]) + '\n')
        f.close()
    else:
        perf_dict = {}
        f = open(file_perf_dic, mode='r')
        for line in f:
            perf_dict[line.split(',')[0]] = int(line.split(',')[1])
        f.close()
    return perf_dict


def create_perf_dictionary(content):
    if content.about.name == 'VMware vCenter Server':
        perf_dict = write_perf_dictionary(content, '/tmp/vcenter_perfdic.txt')
    elif content.about.name == 'VMware ESXi':
        perf_dict = write_perf_dictionary(content, '/tmp/host_perfdic.txt')
    return perf_dict


def main():
    args = GetArgs()
    try:
        entity = args.entity
        if args.counter != 'core' and args.counter != 'status':
            warning = int(args.warning)
            critical = int(args.critical)
        si = None
        if args.password:
            password = args.password
        else:
            password = getpass.getpass(prompt="Enter password for host {} and user {}: ".format(args.host, args.user))
        try:
            si = SmartConnect(host=args.host,
                              user=args.user,
                              pwd=password,
                              port=int(args.port))
        except IOError as e:
            pass
        if not si:
            print('Could not connect to the specified host using specified username and password')
            return -1

        atexit.register(Disconnect, si)
        content = si.RetrieveContent()

        perf_dict = create_perf_dictionary(content)

        if args.type == 'vm':
            #Find VM supplied as arg and use Managed Object Reference (moref) for the PrintVmInfo
            vmProps = GetProperties(content, [vim.VirtualMachine], ['name', 'runtime.powerState'], vim.VirtualMachine)
            for vm in vmProps:
                if (vm['name'] == entity) and (vm['runtime.powerState'] == "poweredOn"):
                    vmObj = vm['moref']
                    if args.counter == 'core':
                        vmCore(vmObj)
                    elif args.counter == 'status':
                        vmStatus(vmObj)
                    elif args.counter == 'cpu.ready':
                        CpuReady(vmObj, content, perf_dict, warning, critical)
                    elif args.counter == 'cpu.usage':
                        CpuUsage(vmObj, content, perf_dict, warning, critical)
                    elif args.counter == 'mem.active':
                        MemoryActive(vmObj, content, perf_dict, warning, critical)
                    elif args.counter == 'mem.shared':
                        MemoryShared(vmObj, content, perf_dict, warning, critical)
                    elif args.counter == 'mem.balloon':
                        MemoryBalloon(vmObj, content, perf_dict, warning, critical)
                    elif args.counter == 'datastore.ioread':
                        DataStoreIORead(vmObj, content, perf_dict, warning, critical)
                    elif args.counter == 'datastore.iowrite':
                        DataStoreIOWrite(vmObj, content, perf_dict, warning, critical)
                    else:
                        print "No supported counter found"
                elif (vm['name'] == entity) and ((vm['runtime.powerState'] == "poweredOff") or (vm['runtime.powerState'] == "suspended")):
                    vmObj = vm['moref']
                    if args.counter == 'core':
                        vmCore(vmObj)
                    elif args.counter == 'status':
                        vmStatus(vmObj)
                    else:
                        print "Virtual Machine is powered off"
                        exit(STATE_UNKNOWN)

        elif args.type == 'host':
            dsProps = GetProperties(content, [vim.HostSystem], ['name'], vim.HostSystem)
            for host in dsProps:
                if host['name'] == entity:
                    hostObj = host['moref']
                    if args.counter == 'core':
                        hostCore(hostObj)
                    else:
                        print "No supported counter found"

        elif args.type == 'datastore':
            dsProps = GetProperties(content, [vim.Datastore], ['name'], vim.Datastore)
            for datastore in dsProps:
                if datastore['name'] == entity:
                    datastoreObj = datastore['moref']
                    if args.counter == 'status':
                        datastoreStatus(datastoreObj)
                    elif args.counter == 'space':
                        datastoreSpace(datastoreObj, warning, critical)
                    else:
                        print "No supported counter found"

        elif args.type == 'cluster':
            clProps = GetProperties(content, [vim.ClusterComputeResource], ['name'], vim.ClusterComputeResource)
            for cluster in clProps:
                if cluster['name'] == entity:
                    clusterObj = cluster['moref']
                    if args.counter == 'status':
                        clusterStatus(clusterObj)
                    else:
                        print "No supported counter found"

        else:
            print "No supported Entity type provided"

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
