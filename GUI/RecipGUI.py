# -*- coding: latin-1 -*-

#Imports from wx package
import wx,os,sys
from wx.lib.mixins.listctrl import CheckListCtrlMixin, ColumnSorterMixin, ListCtrlAutoWidthMixin
from wx.lib.embeddedimage import PyEmbeddedImage
import wx.lib.agw.pybusyinfo as PBI
from wx.lib.wordwrap import wordwrap
wx.SetDefaultPyEncoding('latin-1') 

#Provided by python
import codecs
from operator import itemgetter
from math import pi
from Queue import Queue, Empty
from multiprocessing import Process, Pipe, freeze_support, cpu_count, allow_connection_pickling
from threading import Thread
import time
import textwrap
import cPickle
from ConfigParser import SafeConfigParser
import StringIO

import numpy as np
import CoolProp.State as CPState
from PDSim.recip.core import Recip
from PDSim.scroll.core import Scroll
from PDSimLoader import RecipBuilder, ScrollBuilder
from PDSim.plot.plots import PlotNotebook
import PDSim

import pdsim_panels
import recip_panels
import scroll_panels
import default_configs

class InfiniteList(object):
    """
    Creates a special list where removing an element just puts it back at the end of the list
    """
    def __init__(self, values):
        self.values = values
        
    def pop(self):
        """
        Return the first element, then put the first element back at the end of the list
        """
        val1 = self.values[0]
        self.values.pop(0)
        self.values.append(val1)
        return val1
       
class RedirectText2Pipe(object):
    def __init__(self, pipe_inlet):
        self.pipe_inlet = pipe_inlet
    def write(self, string):
        self.pipe_inlet.send(string)
    def flush(self):
        return None

class Run1(Process):
    def __init__(self,pipe_std, pipe_abort, pipe_results, simulation):
        Process.__init__(self)
        self.pipe_std = pipe_std
        self.pipe_abort = pipe_abort
        self.pipe_results = pipe_results
        self.sim = simulation
        self._want_abort = False

    def run(self):
        redir = RedirectText2Pipe(self.pipe_std)
        sys.stdout = redir
        sys.stderr = redir
        
        if isinstance(self.sim, Recip):
            self.sim.solve(key_inlet='inlet.1',key_outlet='outlet.2',
            endcycle_callback=self.sim.endcycle_callback,
            heat_transfer_callback=self.sim.heat_transfer_callback,
            lump_energy_balance_callback = self.sim.lump_energy_balance_callback,
            valves_callback =self.sim.valves_callback, 
            OneCycle=False,
            UseNR = True,
            solver_method = self.sim.cycle_integrator_type,
            pipe_abort = self.pipe_abort
            )
        elif isinstance(self.sim, Scroll):
            self.sim.solve(key_inlet='inlet.1',key_outlet='outlet.2',
            step_callback = self.sim.step_callback,
            endcycle_callback=self.sim.endcycle_callback,
            heat_transfer_callback=self.sim.heat_transfer_callback,
            lump_energy_balance_callback = self.sim.lump_energy_balance_callback, 
            OneCycle=False,
            UseNR = False,
            solver_method = self.sim.cycle_integrator_type,
            pipe_abort = self.pipe_abort
            )
        else:
            raise TypeError
        
        #Delete the pipe_abort since it cannot pickle
        if hasattr(self.sim,'pipe_abort'):
            del self.sim.pipe_abort
            del self.sim.FlowStorage
            
        if not self.sim._want_abort:
            #Send simulation result back to calling thread
            print 'About to send recip back to calling thread'
            self.pipe_results.send(self.sim)
            print 'Sent recip back to calling thread'
            #Wait for an acknowledgement of receipt
            while not self.pipe_results.poll():
                print 'Waiting for ack of recipt'
                time.sleep(0.1)
                #Check that you got the right acknowledgement key back
                ack_key = self.pipe_results.recv()
                if not ack_key == 'ACK':
                    raise KeyError
                else:
                    print 'Ack accepted'
                    break
            print 'Sent results back to calling thread'
        else:
            print 'Acknowledging completion of abort'
            self.pipe_abort.send('ACK')
        
class WorkerThreadManager(Thread):
    """
    This manager thread creates all the threads that run.  It checks how many processors are available and runs Ncore-1 processes
    
    Runs are consumed from the 
    """
    def __init__(self, target, simulations, stdout_targets, args = None, done_callback = None, 
                 add_results = None, Ncores = None):
        Thread.__init__(self)
        self.target = target
        self.args = args if args is not None else tuple()
        self.done_callback = done_callback
        self.add_results = add_results
        self.simulations = simulations
        self.stdout_targets = stdout_targets
        self.threadsList = []
        self.stdout_list = InfiniteList(stdout_targets)
        if Ncores is None:
            self.Ncores = cpu_count()-1
        else:
            self.Ncores = Ncores
        if self.Ncores<1:
            self.Ncores = 1
        print "Want to run",len(self.simulations),"simulations in batch mode;",
        print self.Ncores, 'cores available for computation'
            
    def run(self):
        #While simulations left to be run or computation is not finished
        while self.simulations or self.threadsList:
            #Add a new thread if possible (leave one core for main GUI)
            if len(self.threadsList) < self.Ncores and self.simulations:
                #Get the next simulation to be run as a tuple
                simulation = (self.simulations.pop(0),)
                #Start the worker thread
                t = RedirectedWorkerThread(self.target, self.stdout_list.pop(), 
                                          args = simulation+self.args, 
                                          done_callback = self.done_callback, 
                                          add_results = self.add_results)
                t.daemon = True
                t.start()
                self.threadsList.append(t)
                print 'Adding thread;', len(self.threadsList),'threads active' 
            
            for thread_ in reversed(self.threadsList):
                if not thread_.is_alive():
                    print 'Joining zombie thread'
                    thread_.join()
                    self.threadsList.remove(thread_)
                    print 'Thread finished; now', len(self.threadsList),'threads active'
    
    def abort(self):
        """
        Pass the message to quit to all the threads; don't run any that are queued
        """
        dlg = wx.MessageDialog(None,"Are you sure you want to kill the current runs?",caption ="Kill Batch?",style = wx.OK|wx.CANCEL)
        if dlg.ShowModal() == wx.ID_OK:
            message = "Aborting in progress, please wait..."
            busy = PBI.PyBusyInfo(message, parent = None, title = "Aborting")
            #Empty the list of simulations to go
            self.simulations = []
            while self.threadsList:
                for thread_ in self.threadsList:
                    #Send the abort signal
                    thread_.abort()
                    #Wait for it to finish up
                    thread_.join()
            del busy
        dlg.Destroy()
        
class RedirectedWorkerThread(Thread):
    """Worker Thread Class."""
    def __init__(self, target, stdout_target = None,  args = None, kwargs = None, done_callback = None, add_results = None):
        """Init Worker Thread Class."""
        Thread.__init__(self)
        self.target_ = target
        self.stdout_target_ = stdout_target
        self.args_ = args if args is not None else tuple()
        self._want_abort = False
        self.done_callback = done_callback
        self.add_results = add_results
        
    def run(self):
        """
        In this function, actually run the process and pull any output from the 
        pipes while the process runs
        """
        sim = None
        pipe_outlet, pipe_inlet = Pipe(duplex = False)
        pipe_abort_outlet, pipe_abort_inlet = Pipe(duplex = True)
        pipe_results_outlet, pipe_results_inlet = Pipe(duplex = True)

        p = Run1(pipe_inlet, pipe_abort_outlet, pipe_results_inlet, self.args_[0])
        p.daemon = True
        p.start()
        
        while p.is_alive():
                
            #If the manager is asked to quit
            if self._want_abort == True:
                #Tell the process to abort, passes message to simulation run
                pipe_abort_inlet.send(True)
                #Wait until it acknowledges the kill by sending back 'ACK'
                while not pipe_abort_inlet.poll():
                    time.sleep(0.5)
#                   #Collect all display output from process while you wait
                    while pipe_outlet.poll():
                        wx.CallAfter(self.stdout_target_.WriteText, pipe_outlet.recv())
                    print 'Waiting for abort'
                abort_flag = pipe_abort_inlet.recv()
                if abort_flag == 'ACK':
                    break
                else:
                    raise ValueError('abort pipe should have received a value of "ACK"')
                
            #Collect all display output from process
            while pipe_outlet.poll():
                wx.CallAfter(self.stdout_target_.WriteText, pipe_outlet.recv())
                
            #Get back the results from the simulation process if they are waiting
            if pipe_results_outlet.poll():
                sim = pipe_results_outlet.recv()
                pipe_results_outlet.send('ACK')
                
        if self._want_abort == True:
            print self.name+": Process has aborted successfully"
        else:
            wx.CallAfter(self.stdout_target_.WriteText, self.name+": Process is done")
            if sim is not None:
                #Get a unique identifier for the model run for pickling purposes
                curdir = os.path.abspath(os.curdir)
                temp_folder = os.path.join(curdir,'_tmp_')
                
                # Make the temporary folder if doesn't exist
                if not os.path.exists(temp_folder):
                    os.mkdir(temp_folder)
                
                identifier = 'PDSim recip ' + time.strftime('%Y-%m-%d-%H-%M-%S')+'_t'+self.name.split('-')[1]
                file_path = os.path.join(temp_folder, identifier + '.mdl')
                print 'Trying to write to', file_path
                if not os.path.exists(file_path):
                    fName = file_path
                else:
                    i = 65
                    def _file_path(i):
                        return os.path.join(temp_folder, identifier + str(chr(i)) + '.mdl')
                    
                    if os.path.exists(_file_path(i)):
                        while os.path.exists(_file_path(i)):
                            i += 1
                        i -= 1
                    fName = _file_path(i)
                
                #Write it to a binary pickled file for safekeeping
                fp = open(fName, 'wb')
                #del sim.FlowStorage
                print "Warning: removing FlowStorage since it doesn't pickle properly"
                cPickle.dump(sim, fp, protocol = -1)
                fp.close()
                "Send the data back to the GUI"
                wx.CallAfter(self.done_callback, sim)
            else:
                print 'Didnt get any simulation data'
        return 1
        
    def abort(self):
        """abort worker thread."""
        print self.name + ' Thread readying for abort'
        # Method for use by main thread to signal an abort
        self._want_abort = True
    
class InputsToolBook(wx.Toolbook):
    """
    The toolbook that contains the pages with input values
    """
    def __init__(self,parent,configfile,id=-1):
        wx.Toolbook.__init__(self, parent, -1, style=wx.BK_LEFT)
        il = wx.ImageList(32, 32)
        indices=[]
        for imgfile in ['Geometry.png',
                        'MassFlow.png',
                        'MechanicalLosses.png',
                        'StatePoint.png']:
            ico_path = os.path.join('ico',imgfile)
            indices.append(il.Add(wx.Image(ico_path,wx.BITMAP_TYPE_PNG).ConvertToBitmap()))
        self.AssignImageList(il)
        
        parser = SafeConfigParser()
        parser.optionxform = unicode
        
        Main = wx.GetTopLevelParent(self)
        if Main.SimType == 'recip':
            # Make the recip panels.  Name should be consistent with configuration 
            # file section heading
            self.panels=(recip_panels.GeometryPanel(self, 
                                                    configfile,
                                                    name='GeometryPanel'),
                         recip_panels.MassFlowPanel(self,
                                                    configfile,
                                                    name='MassFlowPanel'),
                         recip_panels.MechanicalLossesPanel(self,
                                                            configfile,
                                                            name='MechanicalLossesPanel'),
                         pdsim_panels.StateInputsPanel(self,
                                                       configfile,
                                                       name='StatePanel')
                         )
            
        elif Main.SimType == 'scroll':
            # Make the scroll panels.  Name should be consistent with configuration 
            # file section heading
            self.panels=(scroll_panels.GeometryPanel(self, 
                                                    configfile,
                                                    name='GeometryPanel'),
                         scroll_panels.MassFlowPanel(self,
                                                    configfile,
                                                    name='MassFlowPanel'),
                         scroll_panels.MechanicalLossesPanel(self,
                                                    configfile,
                                                    name='MechanicalLossesPanel'),
                         pdsim_panels.StateInputsPanel(self,
                                                   configfile,
                                                   name='StatePanel')
                         )
        
        
        for Name, index, panel in zip(['Geometry','Mass Flow && Valves','Mechanical','State Points'],indices,self.panels):
            self.AddPage(panel,Name,imageId=index)
            
            
    def set_params(self, simulation):
        """
        Pull all the values out of the child panels, using the values in 
        self.items and the function post_set_params if the panel implements
        it
        """
        for panel in self.panels:
            panel.set_params(simulation)
            if hasattr(panel,'post_set_params'):
                panel.post_set_params(simulation)
    
    def post_set_params(self, simulation):
        for panel in self.panels:
            if hasattr(panel,'post_set_params'):
                panel.post_set_params(simulation)
                
    def collect_parametric_terms(self):
        items = [] 
        for panel in self.panels:
            items += panel.items
        return items
    
class IntegratorChoices(wx.Choicebook):
    def __init__(self, parent, **kwargs):
        wx.Choicebook.__init__(self, parent, id = wx.ID_ANY, **kwargs)
    
        # Build the choicebook items
        self.pageEuler=wx.Panel(self)
        self.AddPage(self.pageEuler,'Simple Euler')
        tt = 'Number of steps to be taken by the Euler solver per cycle'
        self.EulerNlabel, self.EulerN = pdsim_panels.LabeledItem(self.pageEuler,
                                                  label="Number of Steps [-]",
                                                  value='7000',
                                                  tooltip = tt)
        sizer=wx.FlexGridSizer(cols=2,hgap=3,vgap=3)
        sizer.AddMany([self.EulerNlabel, self.EulerN])
        self.pageEuler.SetSizer(sizer)
        
        self.pageHeun=wx.Panel(self)
        self.AddPage(self.pageHeun,'Heun')
        tt ='Number of steps to be taken by the Heun solver per cycle'
        self.HeunNlabel, self.HeunN = pdsim_panels.LabeledItem(self.pageHeun,
                                                  label="Number of Steps [-]",
                                                  value='7000',
                                                  tooltip = tt)
        sizer=wx.FlexGridSizer(cols=2,hgap=3,vgap=3)
        sizer.AddMany([self.HeunNlabel, self.HeunN])
        self.pageHeun.SetSizer(sizer)

        tt = """The maximum allowed absolute error per step of the solver"""
        self.pageRK45=wx.Panel(self)
        self.AddPage(self.pageRK45,'Adaptive Runge-Kutta 4/5')
        self.RK45_eps_label, self.RK45_eps = pdsim_panels.LabeledItem(self.pageRK45,
                                                  label="Maximum allowed error per step [-]",
                                                  value='1e-8',
                                                  tooltip = tt)
        sizer=wx.FlexGridSizer(cols=2,hgap=3,vgap=3)
        sizer.AddMany([self.RK45_eps_label, self.RK45_eps])
        self.pageRK45.SetSizer(sizer)
    
    def set_sim(self, simulation):
        
        if self.GetSelection() == 0:
            simulation.cycle_integrator_type = 'Euler'
            simulation.EulerN = int(self.EulerN.GetValue())
        elif self.GetSelection() == 1:
            simulation.cycle_integrator_type = 'Heun'
            simulation.HeunN = int(self.HeunN.GetValue())
        else:
            simulation.cycle_integrator_type = 'RK45'
            simulation.RK45_eps = float(self.RK45_eps.GetValue())

    def set_from_string(self, config_string):
        """
        config_string will be something like Cycle,Euler,7000 or Cycle,RK45,1e-8
        """
        #Chop off the Cycle,
        config_string = config_string.split(',',1)[1]
        
        SolverType, config = config_string.split(',',1)
        if SolverType == 'Euler':
            self.SetSelection(0)
            self.EulerN.SetValue(config)
        elif SolverType == 'Heun':
            self.SetSelection(1)
            self.HeunN.SetValue(config)
        elif SolverType == 'RK45':
            self.SetSelection(2)
            self.RK45_eps.SetValue(config)
        
    def save_to_string(self):
        if self.GetSelection() == 0:
            return 'Cycle = Cycle,Euler,'+self.EulerN.GetValue()
        elif self.GetSelection() == 1:
            return 'Cycle = Cycle,Heun,'+self.HeunN.GetValue()
        else:
            return 'Cycle = Cycle,RK45,'+self.RK45_eps.GetValue()
        
class SolverInputsPanel(pdsim_panels.PDPanel):
    def __init__(self, parent, configfile,**kwargs):
        pdsim_panels.PDPanel.__init__(self, parent, **kwargs)
    
        self.IC = IntegratorChoices(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Insert(0,self.IC)
        sizer.Layout()
    
        # Loads all the parameters from the config file - 
        # it will call post_get_from_configfile 
        self.get_from_configfile('SolverInputsPanel') 
        
    def post_get_from_configfile(self, key, config_string):
        """
        Build the integrator chooser 
        
        This function will be called by PDPanel.get_from_configfile
        """
        if key == 'Cycle':
            self.IC.set_from_string(config_string)
        
    def post_prep_for_configfile(self):
        return self.IC.save_to_string()+'\n'
        
    def post_set_params(self, simulation):
        self.IC.set_sim(simulation)
            
    def supply_parametric_term(self):
        pass
        
class SolverToolBook(wx.Toolbook):
    def __init__(self,parent,configfile,id=-1):
        wx.Toolbook.__init__(self, parent, -1, style=wx.BK_LEFT)
        il = wx.ImageList(32, 32)
        indices=[]
        for imgfile in ['Geometry.png','MassFlow.png']:
            ico_path = os.path.join('ico',imgfile)
            indices.append(il.Add(wx.Image(ico_path,wx.BITMAP_TYPE_PNG).ConvertToBitmap()))
        self.AssignImageList(il)
        
        items = self.Parent.InputsTB.collect_parametric_terms()
        #Make the recip panels.  Name should be consistent with configuration file
        pane1=SolverInputsPanel(self, configfile, name = 'SolverInputsPanel')
        pane2=pdsim_panels.ParametricPanel(self, configfile, items, name='ParametricPanel')
        self.panels=(pane1,pane2)
        
        for Name,index,panel in zip(['Params','Parametric'],indices,self.panels):
            self.AddPage(panel,Name,imageId=index)
            
    def set_params(self,simulat):
        for panel in self.panels:
            panel.set_params(simulat)
            if hasattr(panel,'post_set_params'):
                panel.post_set_params(simulat)            

class WriteOutputsPanel(wx.Panel):
    def __init__(self,parent):
        wx.Panel.__init__(self,parent)
        
        file_list = ['Temperature', 'Pressure', 'Volume', 'Density','Mass']
        #Create the box
        self.file_list = wx.CheckListBox(self, -1, choices = file_list)
        #Make them all checked
        self.file_list.SetCheckedStrings(file_list)
        
        btn = wx.Button(self,label='Select directory')
        btn.Bind(wx.EVT_BUTTON,self.OnWrite)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.file_list)
        sizer.Add(btn)
        self.SetSizer(sizer)
        
        self.Simulation = None
    
    def set_data(self,Simulation):
        """
        Set the internal simulation data for saving to file
        """
        self.Simulation=Simulation
        
    def OnWrite(self,event):
        """
        Event handler for selection of output folder for writing of files
        """
        dlg = wx.DirDialog(self, "Choose a directory:",
                          style=wx.DD_DEFAULT_STYLE|wx.DD_DIR_MUST_EXIST,
                           #| wx.DD_CHANGE_DIR
                           defaultPath=os.path.abspath(os.curdir)
                           )

        if dlg.ShowModal() == wx.ID_OK:
            self.WriteToFiles(dlg.GetPath())

        # Only destroy a dialog after you're done with it.
        dlg.Destroy()
    
    def WriteToFiles(self,dir_path):
        """
        Write the selected data to files in the folder given by dir_path
        """
        if self.Simulation is None:
            raise ValueError('Simulation data must be provied to WriteOutputsPanel')
        
        outputlist = self.file_list.GetCheckedStrings()
        #List of files that will be over-written
        OWList = [file+'.csv' for file in outputlist if os.path.exists(os.path.join(dir_path,file+'.csv'))]

        if OWList: #if there are any files that might get over-written
            
            dlg = wx.MessageDialog(None,message="The following files will be over-written:\n\n"+'\n'.join(OWList),caption="Confirm Overwrite",style=wx.OK|wx.CANCEL)
            if not dlg.ShowModal() == wx.ID_OK:
                #Don't do anything and return
                return
            
        for file in outputlist:
            if file == 'Pressure':
                xmat = self.Simulation.t
                ymat = self.Simulation.p
                pre = 'p'
            elif file == 'Temperature':
                xmat = self.Simulation.t
                ymat = self.Simulation.T
                pre = 'T'
            elif file == 'Volume':
                xmat = self.Simulation.t
                ymat = self.Simulation.V
                pre = 'V'
            elif file == 'Density':
                xmat = self.Simulation.t
                ymat = self.Simulation.rho
                pre = 'rho'
            elif file == 'Mass':
                xmat = self.Simulation.t
                ymat = self.Simulation.m
                pre = 'm'
            else:
                raise KeyError
            
            #Format for writing (first column is crank angle, following are data)
            joined = np.vstack([xmat,ymat]).T
            
            data_heads = [pre+'['+key+']' for key in self.Simulation.CVs.keys()]
            headers = 'theta [rad],'+ ','.join(data_heads)
            
            def row2string(array):
                return  ','.join([str(dummy) for dummy in array])
            
            rows = [row2string(joined[i,:]) for i in range(joined.shape[0])]
            s = '\n'.join(rows)
            
            #Actually write to file
            print 'writing data to ',os.path.join(dir_path,file+'.csv')
            fp = open(os.path.join(dir_path, file+'.csv'),'w')
            fp.write(headers+'\n')
            fp.write(s)
            fp.close()
            
        print 'You selected: %s\n' % dir_path

class RunToolBook(wx.Panel):
    def __init__(self,parent):
        wx.Panel.__init__(self, parent)
        
        # The running page of the main toolbook
        self.log_ctrl = wx.TextCtrl(self, wx.ID_ANY,
                                    style = wx.TE_MULTILINE|wx.TE_READONLY)
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.Outputtext = wx.StaticText(self,-1,'Temporary text')
        self.cmdAbort = wx.Button(self,-1,'Stop\nAll\nRuns')
        self.cmdAbort.Bind(wx.EVT_BUTTON, self.GetGrandParent().OnStop)
        hsizer = wx.BoxSizer(wx.HORIZONTAL)
        hsizer.Add(self.Outputtext,1)
        hsizer.Add(self.cmdAbort,0)
        sizer.Add(hsizer)
        sizer.Add(wx.StaticText(self,-1,"Output Log:"))
        sizer.Add(self.log_ctrl,1,wx.EXPAND)
        
        nb = wx.Notebook(self)
        self.log_ctrl_thread1 = wx.TextCtrl(nb, wx.ID_ANY,
                                            style = wx.TE_MULTILINE|wx.TE_READONLY)
        self.log_ctrl_thread2 = wx.TextCtrl(nb, wx.ID_ANY,
                                            style = wx.TE_MULTILINE|wx.TE_READONLY)
        self.log_ctrl_thread3 = wx.TextCtrl(nb, wx.ID_ANY,
                                            style = wx.TE_MULTILINE|wx.TE_READONLY)
        
        nb.AddPage(self.log_ctrl_thread1,"Thread #1")
        nb.AddPage(self.log_ctrl_thread2,"Thread #2")
        nb.AddPage(self.log_ctrl_thread3,"Thread #3")
        sizer.Add(nb,1,wx.EXPAND)
        self.write_log_button = wx.Button(self,-1,"Write Log to File")
        
        def WriteLog(event=None):
            FD = wx.FileDialog(None,"Log File Name",defaultDir=os.curdir,
                               style=wx.FD_SAVE|wx.FD_OVERWRITE_PROMPT)
            if wx.ID_OK==FD.ShowModal():
                fp=open(FD.GetPath(),'w')
                fp.write(self.log_ctrl.GetValue())
                fp.close()
            FD.Destroy()
            
        self.write_log_button.Bind(wx.EVT_BUTTON,WriteLog)
        sizer.Add(self.write_log_button,0)
        self.SetSizer(sizer)
        sizer.Layout()
        
class AutoWidthListCtrl(wx.ListCtrl, ListCtrlAutoWidthMixin):
    def __init__(self, parent, ID = wx.ID_ANY, pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=0):
        
        wx.ListCtrl.__init__(self, parent, ID, pos, size, style)
        ListCtrlAutoWidthMixin.__init__(self)
        
class ResultsList(wx.Panel, ColumnSorterMixin):
    def __init__(self, parent, headers, values):
        """
        values: a list of list of values.  Each entry should be as long as the number of headers
        """
        wx.Panel.__init__(self, parent)
        
        self.headers = headers
        self.values = values
        
        self.list = AutoWidthListCtrl(self, 
                                      style=wx.LC_REPORT
                                      | wx.BORDER_NONE
                                      | wx.LC_SORT_ASCENDING
                                      )
        #Build the headers
        for i, header in enumerate(headers):
            self.list.InsertColumn(i, header)
        
        self.data = values
        
        #Add the values one row at a time
        for i, row in enumerate(self.data):
            index = self.list.InsertStringItem(sys.maxint,str(row[0]))
            self.list.SetItemData(index,i)
            for j in range(1,len(row)):
                val = row[j]
                self.list.SetStringItem(index,j,str(val))
            
        #Build the itemDataMap needed for the Sorter mixin
        self.itemDataMap = {}
        for i, row in enumerate(self.data):
            Data = []
            for val in row:
                Data.append(val)
            self.itemDataMap[i] = tuple(Data)
        
        total_width = 0    
        for i in range(len(headers)):
            self.list.SetColumnWidth(i, wx.LIST_AUTOSIZE_USEHEADER)
            total_width+=self.list.GetColumnWidth(i)
            
        width_available = self.Parent.GetSize()[0]
        self.list.SetMinSize((width_available,200))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.list,1,wx.EXPAND)
        
        self.il = wx.ImageList(16, 16)
        SmallUpArrow = PyEmbeddedImage(
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAADxJ"
            "REFUOI1jZGRiZqAEMFGke2gY8P/f3/9kGwDTjM8QnAaga8JlCG3CAJdt2MQxDCAUaOjyjKMp"
            "cRAYAABS2CPsss3BWQAAAABJRU5ErkJggg==")
        SmallDnArrow = PyEmbeddedImage(
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAABHNCSVQICAgIfAhkiAAAAEhJ"
            "REFUOI1jZGRiZqAEMFGke9QABgYGBgYWdIH///7+J6SJkYmZEacLkCUJacZqAD5DsInTLhDR"
            "bcPlKrwugGnCFy6Mo3mBAQChDgRlP4RC7wAAAABJRU5ErkJggg==")
        
        self.sm_up = self.il.Add(SmallUpArrow.GetBitmap())
        self.sm_dn = self.il.Add(SmallDnArrow.GetBitmap())
        self.list.SetImageList(self.il, wx.IMAGE_LIST_SMALL)

        ColumnSorterMixin.__init__(self,len(headers)+1)
        
        self.SetSizer(sizer)
        self.SetAutoLayout(True)
        
    # Used by the ColumnSorterMixin, see wx/lib/mixins/listctrl.py
    def GetListCtrl(self):
        return self.list
    
    # Used by the ColumnSorterMixin, see wx/lib/mixins/listctrl.py
    def GetSortImages(self):
        return (self.sm_dn, self.sm_up)
    
    def AsString(self):
        #Sort the output csv table in the same way as the listctrl
        iCol, direction = self.GetSortState()
        
        #Get a sorted version of self.values sorted by the column used in list
        self.values = sorted(self.values, key=itemgetter(iCol))
        
        header_string = [','.join(self.headers)]
        def tostr(row):
            return [str(r) for r in row]
        rows_string = [','.join(tostr(row)) for row in self.values]
        return '\n'.join(header_string+rows_string)

class ColumnSelectionDialog(wx.Dialog):
    def __init__(self, parent, col_options, cols_selected):
        wx.Dialog.__init__(self,parent,size = (800,350))
        
        self.col_options = col_options

        self.selected = [col_options[col] for col in cols_selected]
        self.not_selected = [col_options[col] for col in col_options if col not in cols_selected]
        
        self.col_library_label = wx.StaticText(self, label = 'Available columns:')
        self.col_used_label = wx.StaticText(self, label = 'Selected columns:')
        self.col_library = wx.ListBox(self, choices = self.not_selected, style = wx.LB_EXTENDED)
        self.col_used = wx.ListBox(self, choices = self.selected, style = wx.LB_EXTENDED)
        self.col_library.SetMinSize((300,300))
        self.col_used.SetMinSize((300,300))
        
        #The central column with add and remove buttons
        self.AddAllButton=wx.Button(self, label='All ->')
        self.RemoveAllButton=wx.Button(self, label='<- All')
        self.AddButton=wx.Button(self, label='-->')
        self.RemoveButton=wx.Button(self, label='<--')
        self.AddButton.Bind(wx.EVT_BUTTON,self.OnAdd)
        self.RemoveButton.Bind(wx.EVT_BUTTON,self.OnRemove)
        self.AddAllButton.Bind(wx.EVT_BUTTON,self.OnAddAll)
        self.RemoveAllButton.Bind(wx.EVT_BUTTON,self.OnRemoveAll)
        vsizer = wx.BoxSizer(wx.VERTICAL)
        vsizer.AddMany([self.AddAllButton, self.RemoveAllButton])
        vsizer.AddSpacer(40)
        vsizer.AddMany([self.AddButton, self.RemoveButton])

        #The far-right column with up,down, ok, cancel buttons      
        self.Up = wx.Button(self, label='Move Up')
        self.Up.Bind(wx.EVT_BUTTON,self.OnUp)
        self.Down = wx.Button(self, label='Move Down')
        self.Down.Bind(wx.EVT_BUTTON,self.OnDown)
        self.OkButton = wx.Button(self, label='Ok')
        self.OkButton.Bind(wx.EVT_BUTTON,self.OnAccept)
        self.CancelButton = wx.Button(self, label='Cancel')
        self.CancelButton.Bind(wx.EVT_BUTTON,self.OnClose)
        vsizer2 = wx.BoxSizer(wx.VERTICAL)
        vsizer2.AddMany([self.Up,self.Down])
        vsizer2.AddSpacer(40)
        vsizer2.AddMany([self.CancelButton, self.OkButton])
        
        #Layout the dialog
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        vsizer0 = wx.BoxSizer(wx.VERTICAL)
        vsizer0.Add(self.col_library_label)
        vsizer0.Add(self.col_library, 1, wx.EXPAND)
        sizer.Add(vsizer0)
        sizer.AddSpacer(10)
        sizer.Add(vsizer,0,wx.ALIGN_CENTER_VERTICAL)
        sizer.AddSpacer(10)
        vsizer20 = wx.BoxSizer(wx.VERTICAL)
        vsizer20.Add(self.col_used_label)
        vsizer20.Add(self.col_used, 1, wx.EXPAND)
        sizer.Add(vsizer20)
        sizer.AddSpacer(10)
        sizer.Add(vsizer2,0,wx.ALIGN_CENTER_VERTICAL)
        self.SetSizer(sizer)
        sizer.Layout()
        
        #Bind a key-press event to all objects to get Esc key press
        children = self.GetChildren()
        for child in children:
            child.Bind(wx.EVT_KEY_UP,  self.OnKeyPress) 

    def OnKeyPress(self,event):
        """ cancel if Escape key is pressed """
        event.Skip()
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        elif event.GetKeyCode() == wx.WXK_RETURN:
            self.EndModal(wx.ID_OK)
        
    def label2attr(self,label):
        for col in self.col_options:
            if self.col_options[col] == label:
                return col
        raise KeyError
        
    def OnAccept(self, event):
        self.EndModal(wx.ID_OK)
        
    def OnClose(self,event):
        self.EndModal(wx.ID_CANCEL)
        
    def OnAddAll(self, event):
        self.selected += self.not_selected
        self.not_selected = []
        self.col_library.SetItems(self.not_selected)
        self.col_used.SetItems(self.selected)
        
    def OnRemoveAll(self, event):
        self.not_selected += self.selected
        self.selected = []
        self.col_library.SetItems(self.not_selected)
        self.col_used.SetItems(self.selected)
        
    def OnAdd(self, event):
        indices = self.col_library.GetSelections()
        labels = [self.col_library.GetString(index) for index in indices]

        for label in reversed(labels):
            i = self.not_selected.index(label)
            self.selected.append(self.not_selected.pop(i))
        self.col_library.SetItems(self.not_selected)
        self.col_used.SetItems(self.selected)
        
    def OnRemove(self, event):
        indices = self.col_used.GetSelections()
        labels = [self.col_used.GetString(index) for index in indices]

        for label in reversed(labels):
            i = self.selected.index(label)
            self.not_selected.append(self.selected.pop(i))
        self.col_library.SetItems(self.not_selected)
        self.col_used.SetItems(self.selected)
        
    def OnUp(self, event):
        indices = self.col_used.GetSelections()
        labels = [self.col_used.GetString(index) for index in indices]
        for label in labels:
            i = self.selected.index(label)
            if i>0:
                #swap item and the previous item
                self.selected[i-1],self.selected[i]=self.selected[i],self.selected[i-1]
        self.col_used.SetItems(self.selected)
        if len(labels) == 1:
            self.col_used.SetSelection(indices[0]-1)
    
    def OnDown(self, event):
        indices = self.col_used.GetSelections()
        labels = [self.col_used.GetString(index) for index in indices]
        for label in labels:
            i = self.selected.index(label)
            if i<len(self.selected)-1:
                #swap item and the next item
                self.selected[i+1],self.selected[i]=self.selected[i],self.selected[i+1]
        self.col_used.SetItems(self.selected)
        if len(labels) == 1:
            self.col_used.SetSelection(indices[0]+1)
    
    def GetSelections(self):
        labels = self.col_used.GetStrings()
        attrs = [self.label2attr(label) for label in labels]
        return attrs

class FileOutputDialog(wx.Dialog):
    def __init__(self,Simulations, table_string):
        wx.Dialog.__init__(self,None)
        self.Simulations = Simulations
        self.table_string = table_string
        
        #The root directory selector
        hsizer = wx.BoxSizer(wx.HORIZONTAL)
        hsizer.Add(wx.StaticText(self,label="Output Directory:"))
        self.txtDir = wx.TextCtrl(self,value ='.')
        self.txtDir.SetMinSize((200,-1))
        hsizer.Add(self.txtDir,1,wx.EXPAND)
        self.cmdDirSelect = wx.Button(self,label="Select...")
        self.cmdDirSelect.Bind(wx.EVT_BUTTON,self.OnDirSelect)
        hsizer.Add(self.cmdDirSelect)
        
        
        #The CSV selections
        file_list = ['Temperature', 'Pressure', 'Volume', 'Density','Mass']
        #Create the box
        self.file_list = wx.CheckListBox(self, choices = file_list)
        #Make them all checked
        self.file_list.SetCheckedStrings(file_list)
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(hsizer)
        sizer.AddSpacer(10)
        sizer.Add(wx.StaticText(self,label='CSV files:'))
        sizer.Add(self.file_list)
        
        sizer.AddSpacer(10)
        self.chkPickled = wx.CheckBox(self,label='Pickled data files (Warning! Can be quite large)')
        self.chkPickled.SetValue(True)
        sizer.Add(self.chkPickled)
        
        sizer.AddSpacer(10)
        self.chkTable = wx.CheckBox(self,label='Tabular data')
        self.chkTable.SetValue(True)
        sizer.Add(self.chkTable)
        
        self.cmdWrite = wx.Button(self, label = 'Write!')
        self.cmdWrite.Bind(wx.EVT_BUTTON, self.OnWrite)
        sizer.AddSpacer(10)
        sizer.Add(self.cmdWrite)
        self.SetSizer(sizer)
        
    def OnDirSelect(self,event):
        #
        os.chdir(os.curdir)
        dlg = wx.DirDialog(None, "Choose a directory:",
                           defaultPath = os.path.abspath(os.curdir),
                           style=wx.DD_DEFAULT_STYLE | wx.DD_NEW_DIR_BUTTON
                           )
        if dlg.ShowModal() == wx.ID_OK:
            self.txtDir.SetValue(dlg.GetPath())
        dlg.Destroy()
        
    def OnWrite(self, event):
        """
        
        """
        dir_path = self.txtDir.GetValue()
        if not os.path.exists(dir_path):
            dlg = wx.MessageDialog(None, message = 'Selected output directory does not exist.  Please select a folder then try again')
            dlg.ShowModal()
            dlg.Destroy()
            return
        
        for i, sim in enumerate(self.Simulations):
            if (self.file_list.GetCheckedStrings() or 
                self.chkPickled.GetValue()):
                
                run_path = 'RunNumber{0:04d}'.format(i+1)
                if not os.path.exists(os.path.join(dir_path, run_path)):
                    os.mkdir(os.path.join(dir_path, run_path))
                self.write_csv_files(os.path.join(dir_path, run_path), sim)
            
            if self.chkPickled.GetValue():
                self.write_pickle(os.path.join(dir_path, run_path), sim)
            if self.chkTable.GetValue():
                fp = open(os.path.join(dir_path,'ResultsTable.csv'),'w')
                fp.write(self.table_string)
                fp.close()
        self.Destroy()
    
    def write_pickle(self, dir_path, sim):
        fp = open(os.path.join(dir_path,'PickledSimulation.mdl'),'wb')
        cPickle.dump(sim, fp, -1)
        fp.close()
        
    def write_csv_files(self, dir_path, sim):
        """
        Write the selected data to files in the folder given by dir_path
        """
        
        outputlist = self.file_list.GetCheckedStrings()
            
        #List of files that will be over-written
        OWList = [file+'.csv' for file in outputlist if os.path.exists(os.path.join(dir_path, file+'.csv'))]

        if OWList: #if there are any files that might get over-written
            
            dlg = wx.MessageDialog(None, message="The following files will be over-written:\n\n"+'\n'.join(OWList),caption="Confirm Overwrite",style=wx.OK|wx.CANCEL)
            if not dlg.ShowModal() == wx.ID_OK:
                #Don't do anything and return
                return wx.ID_CANCEL

        for file in outputlist:
            if file == 'Pressure':
                xmat = sim.t
                ymat = sim.p
                pre = 'p'
            elif file == 'Temperature':
                xmat = sim.t
                ymat = sim.T
                pre = 'T'
            elif file == 'Volume':
                xmat = sim.t
                ymat = sim.V
                pre = 'V'
            elif file == 'Density':
                xmat = sim.t
                ymat = sim.rho
                pre = 'rho'
            elif file == 'Mass':
                xmat = sim.t
                ymat = sim.m
                pre = 'm'
            else:
                raise KeyError
            
            #Format for writing (first column is crank angle, following are data)
            joined = np.vstack([xmat,ymat]).T
            
            data_heads = [pre+'['+key+']' for key in sim.CVs.keys()]
            headers = 'theta [rad],'+ ','.join(data_heads)
            
            def row2string(array):
                return  ','.join([str(dummy) for dummy in array])
            
            rows = [row2string(joined[i,:]) for i in range(joined.shape[0])]
            s = '\n'.join(rows)
            
            #Actually write to file
            print 'writing data to ',os.path.join(dir_path,file+'.csv')
            fp = open(os.path.join(dir_path,file+'.csv'),'w')
            fp.write(headers+'\n')
            fp.write(s)
            fp.close()
    
class OutputDataPanel(pdsim_panels.PDPanel):
    def __init__(self, parent, variables, configfile, **kwargs):
        pdsim_panels.PDPanel.__init__(self, parent, **kwargs)
        
        self.results = []
        
        self.variables = variables
        #Make the items
        cmdLoad = wx.Button(self, label = "Add Runs...")
        cmdRefresh = wx.Button(self, label = "Refresh")
        cmdSelect = wx.Button(self, label = "Select Columns...")
        #Make the sizers
        hsizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer = wx.BoxSizer(wx.VERTICAL)
        #Put the things into sizers
        hsizer.Add(cmdLoad)
        hsizer.Add(cmdSelect)
        hsizer.Add(cmdRefresh)
        sizer.Add(hsizer)
        #Bind events
        cmdLoad.Bind(wx.EVT_BUTTON, self.OnLoadRuns)
        cmdSelect.Bind(wx.EVT_BUTTON, self.OnSelectCols)
        cmdRefresh.Bind(wx.EVT_BUTTON, self.OnRefresh)
        
        self.SetSizer(sizer)
        self.ResultsList = None
        self.WriteButton = None
        
        #Create a list of possible columns
        self.column_options = {'mdot': 'Mass flow rate [kg/s]',
                               'eta_v': 'Volumetric efficiency [-]',
                               'eta_a': 'Adiabatic efficiency [-]',
                               'Td': 'Discharge temperature [K]',
                               'Wdot': 'Shaft power [kW]',
                               'Wdot_motor': 'Motor losses [kW]',
                               'Wdot_electrical': 'Electrical power [kW]',
                               'Wdot_mechanical': 'Mechanical losses [kW]',
                               'Qdot_from_gas': 'Heat transfer from gas [kW]',
                               'Qamb': 'Ambient heat transfer [kW]',
                               'run_index': 'Run Index',
                               'eta_oi': 'Overall isentropic efficiency [-]'
                               }
        
        for var in self.variables:
            key = var['attr']
            value = var['text']
            self.column_options[key] = value
        
        self.columns_selected = []
        sizer.Layout()
        
        self.Bind(wx.EVT_SIZE, self.OnRefresh)
        
        self.get_from_configfile('OutputDataPanel')
        
    def rebuild(self):
        
        if self.results: #as long as it isn't empty
            
            if self.ResultsList is not None:
                self.WriteButton.Destroy()
                self.RemoveButton.Destroy()
                self.ResultsList.Destroy()
                self.GetSizer().Layout()
                
            rows = []
            for sim in self.results: #loop over the results
                row = []
                for attr in self.columns_selected:
                    if hasattr(sim, attr):
                        value = getattr(sim,attr)
                        row.append(value)
                    else:
                        raise KeyError(attr + ' is an invalid header attribute')
                rows.append(row)
            headers = [self.column_options[attr] for attr in self.columns_selected]
            
            #The items being created
            self.ResultsList = ResultsList(self, headers, rows)

            
            self.WriteButton = wx.Button(self, label = 'Write to file...')
            self.WriteButton.Bind(wx.EVT_BUTTON, self.OnWriteFiles)
            
            self.RemoveButton = wx.Button(self,label = 'Remove selected')
            self.RemoveButton.Bind(wx.EVT_BUTTON, self.OnRemoveSelected)
            
            
            #Do the layout of the panel
            sizer = self.GetSizer()
            hsizer = wx.BoxSizer(wx.HORIZONTAL)
            hsizer.Add(self.ResultsList,1,wx.EXPAND)
            sizer.Add(hsizer)
            
            hsizer = wx.BoxSizer(wx.HORIZONTAL)
            hsizer.Add(self.WriteButton)
            hsizer.Add(self.RemoveButton)
            sizer.Add(hsizer)
            
            sizer.Layout()
            self.Refresh()
    
    def add_runs(self, results, rebuild = False):
        self.results += results
        if rebuild:
            self.rebuild()
        
    def OnLoadRuns(self, event = None):
        FD = wx.FileDialog(None,"Load Runs",defaultDir='_tmp_',
                           wildcard = 'PDSim Runs (*.mdl)|*.mdl',
                           style=wx.FD_OPEN|wx.FD_MULTIPLE|wx.FD_FILE_MUST_EXIST)
        if wx.ID_OK == FD.ShowModal():
            file_paths = FD.GetPaths()
            for file in file_paths:
                sim = cPickle.load(open(file,'rb'))
                self.add_runs([sim])
            self.rebuild()
        FD.Destroy()
        
    def OnRemoveSelected(self, event):
        list_ = self.ResultsList.GetListCtrl()
        
        indices = []
        index = list_.GetFirstSelected()
        while index != -1:
            indices.append(index)
            index = list_.GetNextSelected(index)
            
        #Some runs to delete
        if indices:
            #Warn before removing
            dlg = wx.MessageDialog(None,'You are about to remove '+str(len(indices))+' runs.  Ok to confirm', style = wx.OK|wx.CANCEL)
            if dlg.ShowModal() == wx.ID_OK:
                for index in reversed(indices):
                    self.results.pop(index)
            dlg.Destroy()
            self.rebuild()
                
    
    def OnWriteFiles(self, event):
        """
        Event that fires when the button is clicked to write a selection of things to files
        """
        table_string = self.ResultsList.AsString()
        dlg = FileOutputDialog(self.results, table_string = table_string)
        dlg.ShowModal()
        dlg.Destroy()
        
    def OnRefresh(self, event):
        self.rebuild()
        
    def OnSelectCols(self, event = None):
        dlg = ColumnSelectionDialog(None, self.column_options, self.columns_selected)
        if dlg.ShowModal() == wx.ID_OK:
            self.columns_selected = dlg.GetSelections() 
        dlg.Destroy()
        self.rebuild()
    
    def post_get_from_configfile(self, key, value):
        if not key == 'selected':
            raise KeyError
        
        list_str = value.split(',',1)[1].replace("'","").replace('[','').replace(']','').replace("u'","'")
        for attr in list_str.split(';'):
            #Strip leading and trailing space
            attr = attr.strip()
            self.columns_selected.append(attr)
    
    def post_prep_for_configfile(self):
        return 'selected  = selected,'+str(self.columns_selected).replace(',',';').replace("u'","'")+'\n'
        
class OutputsToolBook(wx.Toolbook):
    def __init__(self,parent,configfile):
        wx.Toolbook.__init__(self, parent, wx.ID_ANY, style=wx.BK_LEFT)
        il = wx.ImageList(32, 32)
        indices=[]
        for imgfile in ['Geometry.png','MassFlow.png']:
            ico_path = os.path.join('ico',imgfile)
            indices.append(il.Add(wx.Image(ico_path,wx.BITMAP_TYPE_PNG).ConvertToBitmap()))
        self.AssignImageList(il)
        
        variables = self.Parent.InputsTB.collect_parametric_terms()
        self.PlotsPanel = wx.Panel(self)
        self.DataPanel = OutputDataPanel(self,
                                         variables = variables, 
                                         name = 'OutputDataPanel',
                                         configfile = configfile)
        
        #Make a Recip instance
        self.panels=(self.DataPanel,self.PlotsPanel)
        for Name,index,panel in zip(['Data','Plots'],indices,self.panels):
            self.AddPage(panel,Name,imageId=index)
            
        self.PN = None
            
    def plot_outputs(self, recip=None):
        parent = self.PlotsPanel
        # First call there is no plot notebook in existence
        if self.PN is None:
            self.PN = PlotNotebook(recip,parent)
            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(self.PN,1,wx.EXPAND)
            parent.SetSizer(sizer)
            parent.Fit() ##THIS IS VERY IMPORTANT!!!!!!!!!!! :)
        else:
            self.PN.update(recip)
            
class MainToolBook(wx.Toolbook):
    def __init__(self,parent,configfile):
        wx.Toolbook.__init__(self, parent, -1, style=wx.BK_TOP)
        il = wx.ImageList(32, 32)
        indices=[]
        for imgfile in ['Inputs.png','Solver.png','Solver.png','Outputs.png']:
            ico_path = os.path.join('ico',imgfile)
            indices.append(il.Add(wx.Image(ico_path,wx.BITMAP_TYPE_PNG).ConvertToBitmap()))
        self.AssignImageList(il)
        
        self.InputsTB = InputsToolBook(self, configfile)
        self.SolverTB = SolverToolBook(self, configfile)
        self.RunTB = RunToolBook(self)
        self.OutputsTB = OutputsToolBook(self, configfile)
        
        self.panels=(self.InputsTB,self.SolverTB,self.RunTB,self.OutputsTB)
        for Name,index,panel in zip(['Inputs','Solver','Run','Output'],indices,self.panels):
            self.AddPage(panel,Name,imageId=index)

class MainFrame(wx.Frame):
    def __init__(self, configfile=None, position=None, size=None):
        wx.Frame.__init__(self, None, title = "PDSim GUI", size=(700, 700))
        
        if configfile is None: #No file name or object passed in
            
            #First see if there is a file at configs/default.cfg
            configfile = os.path.join('configs','default.cfg')
            if os.path.exists(configfile):
                configbuffer = open(configfile,'rb')
                
            #Command line option provided
            elif '--config' in sys.argv:
                i = sys.argv.index('--config')
                configfile = sys.argv[i+1]
                configbuffer = open(configfile, 'rb') 
                
            #Then use the internal default recip
            else:
                configbuffer = default_configs.get_scroll_defaults()
        
        elif isinstance(configfile, basestring):
            if os.path.exists(configfile):
                configbuffer = open(configfile, 'rb')
                        
        elif isinstance(configfile, StringIO.StringIO):
            configbuffer = configfile
                
        else:
            raise ValueError
        
        #Get a unicode-wrapped version of the selected file-like object
        enc, dec, reader, writer = codecs.lookup('latin-1')
        uConfigParser = reader(configbuffer)
                
        #The file-like object for the configuration
        self.config_parser = SafeConfigParser()
        self.config_parser.optionxform = unicode 
        self.config_parser.readfp(uConfigParser)
        
        #The file-like object for the default scroll configuration
        parser = SafeConfigParser()
        parser.optionxform = unicode
        uConfigParser = reader(default_configs.get_scroll_defaults())
        parser.readfp(uConfigParser)
        self.config_parser_default_scroll = parser
        
        #The file-like object for the default recip configuration
        parser = SafeConfigParser()
        parser.optionxform = unicode
        uConfigParser = reader(default_configs.get_recip_defaults())
        parser.readfp(uConfigParser)
        self.config_parser_default_recip = parser
        
        #Get the simulation type (recip, scroll, ...)
        self.SimType = self.config_parser.get('Globals', 'Type')
            
        #The position and size are needed when the frame is rebuilt, but not otherwise
        if position is None:
            position = (-1,-1)
        if size is None:
            size = (-1,-1)
        
        #Use the builder function to rebuild using the configuration objects
        self.build()
        
        # Set up redirection of input and output to logging wx.TextCtrl
        # Taken literally from http://www.blog.pythonlibrary.org/2009/01/01/wxpython-redirecting-stdout-stderr/
        class RedirectText(object):
            def __init__(self,aWxTextCtrl):
                self.out=aWxTextCtrl
            def write(self, string):
                wx.CallAfter(self.out.WriteText, string)
            def flush(self):
                return None
                
        redir=RedirectText(self.MTB.RunTB.log_ctrl)
        sys.stdout=redir
        sys.stderr=redir
        
        self.SetPosition(position)
        self.SetSize(size)
        
        self.worker = None
        self.workers = None
        self.WTM = None
        
        #A thread-safe queue for the processing of the results 
        self.results_list = Queue()
        
        # Bind the idle event handler that will always run and
        # deal with the results
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnIdle, self.timer)
        self.timer.Start(1000) #1000 ms between checking the queue
        
        self.default_configfile = default_configs.get_recip_defaults()
        
    def get_config_objects(self):
        if self.SimType == 'recip':
            return (self.config_parser, self.config_parser_default_recip)
        elif self.SimType == 'scroll':
            return (self.config_parser, self.config_parser_default_scroll)
        else:
            raise AttributeError
    
    def get_logctrls(self):
        return [self.MTB.RunTB.log_ctrl_thread1,
                self.MTB.RunTB.log_ctrl_thread2,
                self.MTB.RunTB.log_ctrl_thread3]
    
    def rebuild(self, configfile):
        """
        Destroy everything in the main frame and recreate 
        the contents based on parsing the config file
        """
        # Create a new instance of the MainFrame class using the 
        # new configuration file name and the current location of
        # the frame
        position = self.GetPosition()
        size = self.GetSize()
        frame = MainFrame(configfile, position=position, size=size)
        frame.Show()
        
        #Destroy the current MainFrame
        self.Destroy()
        
    def build(self):
        self.make_menu_bar()
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.MTB=MainToolBook(self, self.get_config_objects())
        sizer.Add(self.MTB, 1, wx.EXPAND)
        self.SetSizer(sizer)
        sizer.Layout()
        
        self.worker=None
        self.Layout() 
        
        if self.SimType == 'scroll':
            dlg = wx.MessageDialog(None,"Sorry but scroll compressor is a work in progress")
            dlg.ShowModal()
            dlg.Destroy()
        
    def build_recip(self):
        #Instantiate the recip class
        recip=Recip()
        #Pull things from the GUI as much as possible
        self.MTB.InputsTB.set_params(recip)
        self.MTB.SolverTB.set_params(recip)
        #Build the model the rest of the way
        RecipBuilder(recip)
        return recip
    
    def build_scroll(self):
        #Instantiate the scroll class
        scroll=Scroll()
        #Pull things from the GUI as much as possible
        self.MTB.InputsTB.set_params(scroll)
        self.MTB.SolverTB.set_params(scroll)
        #Build the model the rest of the way
        ScrollBuilder(scroll)
        return scroll
    
    def run_simulation(self, sim):
        """
        Run a single simulation
        """
        #Make single-run into a list in order to use the code
        self.run_batch([sim])
    
    def run_batch(self, sims):
        """
        Run a list of simulations
        """
        if self.WTM is None:
            self.MTB.SetSelection(2)
            self.WTM = WorkerThreadManager(Run1, sims, self.get_logctrls(),args = tuple(), done_callback = self.deliver_result)
            self.WTM.setDaemon(True)
            self.WTM.start()
        else:
            dlg = wx.MessageDialog(None,"Batch has already started.  Wait until completion or kill the batch","")
            dlg.ShowModal()
            dlg.Destroy()
            
    def deliver_result(self, sim = None):
        if sim is not None:
#            print 'Queueing a result for further processing'
            self.results_list.put(sim)
            print 'Result queued' 
        
    ################################
    #         Event handlers       #
    ################################
       
    def OnConfigOpen(self,event):
        FD = wx.FileDialog(None,"Load Configuration file",defaultDir='configs',
                           style=wx.FD_OPEN)
        if wx.ID_OK==FD.ShowModal():
            file_path=FD.GetPath()
            #Now rebuild the GUI using the desired configuration file
            self.rebuild(file_path)
        FD.Destroy()
        
    def OnConfigSave(self,event):
        FD = wx.FileDialog(None,"Save Configuration file",defaultDir='configs',
                           style=wx.FD_SAVE|wx.FD_OVERWRITE_PROMPT)
        if wx.ID_OK==FD.ShowModal():
            file_path=FD.GetPath()
            print 'Writing configuration file to ', file_path   
            #Build the config file entry
            string_list = []
            
            #Based on mode selected in menu select type to be written to file
            if self.TypeRecip.IsChecked():
                Type = 'recip'
            elif self.TypeScroll.IsChecked():
                Type = 'scroll'
            else:
                raise ValueError
            
            if self.TypeCompressor.IsChecked():
                mode = 'compressor'
            elif self.TypeExpander.IsChecked():
                mode = 'expander'
            else:
                raise ValueError
            
            #Header information
            header_string_template = textwrap.dedent(
                 """
                 [Globals]
                 Type = {CompressorType}
                 Mode = {Mode}
                 """
                 ) 
            terms = dict(CompressorType = Type, Mode = mode)
            header_string = header_string_template.format(**terms)

            string_list.append(unicode(header_string,'latin-1'))
            
            for panel in self.MTB.InputsTB.panels + self.MTB.SolverTB.panels + self.MTB.OutputsTB.panels:
                #Skip any panels that do not subclass PDPanel
                if not isinstance(panel,pdsim_panels.PDPanel):
                    continue
                
                panel_string = panel.prep_for_configfile()
                if isinstance(panel_string,str):
                    string_list.append(unicode(panel_string,'latin-1'))
                elif isinstance(panel_string,unicode):
                    #Convert to a string
                    panel_string = unicode.decode(panel_string,'latin-1')
                    string_list.append(panel_string)
            
            fp = codecs.open(file_path,'w',encoding = 'latin-1')
            fp.write(u'\n'.join(string_list))
            fp.close()
        FD.Destroy()
        
    def OnStart(self, event):
        """
        Runs the primary inputs without applying the parametric table inputs
        """
        print "Running.."
        self.MTB.SetSelection(2)
        if self.SimType == 'recip':
            self.recip = self.build_recip()
            self.recip.run_index = 1
            self.run_simulation(self.recip)
        
        elif self.SimType == 'scroll':
            self.scroll = self.build_scroll()
            self.scroll.run_index = 1
            self.run_simulation(self.scroll)
            
    def OnStop(self, event):
        """Stop Computation."""
        if self.WTM is not None:
            self.WTM.abort()
        
    def OnQuit(self, event):
        self.Close()
        
    def OnIdle(self, event):
        """
        Do the things that are needed when the GUI goes idle
        
        This is only run every once in a while (see __init__) for performance-sake 
        """
        
        #Add results from the pipe to the GUI
        if not self.results_list.empty():
            print 'readying to get simulation'
            sim = self.results_list.get()
            print 'got a simulation'

            self.MTB.OutputsTB.plot_outputs(sim)
            self.MTB.OutputsTB.DataPanel.add_runs([sim])
            self.MTB.OutputsTB.DataPanel.rebuild()
            #Check whether there are no more results to be processed and threads list is empty
            #This means the manager has completed its work - reset it
            if self.results_list.empty() and not self.WTM.threadsList:
                self.WTM = None
        
        if self.results_list.empty() and self.WTM is not None and not self.WTM.threadsList:
            self.WTM = None
        
    def OnRunFinish(self, sim = None):
        #Collect the runs
        
        if sim is not None:
            wx.CallAfter(sys.stdout.write,'called OnRunFinish with sim data\n')
            self.MTB.OutputsTB.plot_outputs(sim)
            self.MTB.OutputsTB.DataPanel.add_runs([sim])
            self.MTB.OutputsTB.DataPanel.rebuild()
        else:
            wx.CallAfter(sys.stdout.write,'called OnRunFinish without sim data\n')
        """
        Each time a run completes, if the list of running threads is empty,
        remove the thread manager 
        """
        if self.WTM is not None:
            if not self.WTM.threadsList:
                wx.CallAfter(sys.stdout.write,'Empty\n')
                self.WTM = None
            else:
                wx.CallAfter(sys.stdout.write,'Not Empty Yet\n')
    
    def OnAbout(self, event = None):
        if "unicode" in wx.PlatformInfo:
            wx_unicode = '\nwx Unicode support: True\n'
        else:
            wx_unicode = '\nwx Unicode support: False\n'
        import CoolProp
        info = wx.AboutDialogInfo()
        info.Name = "PDSim GUI"
        info.Version = PDSim.__version__
        info.Copyright = "(C) 2012 Ian Bell"
        info.Description = wordwrap(
            "A graphical user interface for the PDSim model\n\n"+
            "wx version: "+wx.__version__+
            wx_unicode+
            "CoolProp version: "+CoolProp.__version__,
            350, wx.ClientDC(self))
        info.WebSite = ("http://pdsim.sourceforge.net", "PDSim home page")
        info.Developers = [ "Ian Bell", "Craig Bradshaw"]

        # Then we call wx.AboutBox giving it that info object
        wx.AboutBox(info)
        
    def OnChangeSimType(self, event):  
        if self.TypeScroll.IsChecked():
            print 'Scroll-type compressor'
            self.rebuild(default_configs.get_scroll_defaults())
        elif self.TypeRecip.IsChecked():
            print 'Recip-type compressor'
            self.rebuild(default_configs.get_recip_defaults())
        
    def make_menu_bar(self):
        #################################
        ####       Menu Bar         #####
        #################################
        
        # Menu Bar
        self.MenuBar = wx.MenuBar()
        
        self.File = wx.Menu()
        self.menuFileOpen = wx.MenuItem(self.File, -1, "Open Config from file...\tCtrl+O", "", wx.ITEM_NORMAL)
        self.menuFileSave = wx.MenuItem(self.File, -1, "Save config to file...\tCtrl+S", "", wx.ITEM_NORMAL)
        self.menuFileQuit = wx.MenuItem(self.File, -1, "Quit\tCtrl+Q", "", wx.ITEM_NORMAL)
        self.File.AppendItem(self.menuFileOpen)
        self.File.AppendItem(self.menuFileSave)
        self.File.AppendItem(self.menuFileQuit)
        self.MenuBar.Append(self.File, "File")
        self.Bind(wx.EVT_MENU,self.OnConfigOpen,self.menuFileOpen)
        self.Bind(wx.EVT_MENU,self.OnConfigSave,self.menuFileSave)
        self.Bind(wx.EVT_MENU,self.OnQuit,self.menuFileQuit)
        
        self.Type = wx.Menu()
        self.TypeRecip = wx.MenuItem(self.Type, -1, "Recip", "", wx.ITEM_RADIO)
        self.TypeScroll = wx.MenuItem(self.Type, -1, "Scroll", "", wx.ITEM_RADIO)
        self.TypeCompressor = wx.MenuItem(self.Type, -1, "Compressor mode", "", wx.ITEM_RADIO)
        self.TypeExpander = wx.MenuItem(self.Type, -1, "Expander mode", "", wx.ITEM_RADIO)
        self.TypeCompressor.Enable(False)
        self.TypeExpander.Enable(False)
        self.Type.AppendItem(self.TypeRecip)
        self.Type.AppendItem(self.TypeScroll)
        self.Type.AppendSeparator()
        self.Type.AppendItem(self.TypeCompressor)
        self.Type.AppendItem(self.TypeExpander)
        
        self.MenuBar.Append(self.Type, "Type")
        
        
        if self.config_parser.get('Globals', 'Type') == 'recip':
            self.TypeRecip.Check(True)
        else:
            self.TypeScroll.Check(True)
            
        self.Bind(wx.EVT_MENU,self.OnChangeSimType,self.TypeScroll)
        self.Bind(wx.EVT_MENU,self.OnChangeSimType,self.TypeRecip)
        
        self.Solve = wx.Menu()
        self.SolveSolve = wx.MenuItem(self.Solve, -1, "Solve\tF5", "", wx.ITEM_NORMAL)
        self.Solve.AppendItem(self.SolveSolve)
        self.MenuBar.Append(self.Solve, "Solve")
        self.Bind(wx.EVT_MENU, self.OnStart, self.SolveSolve)
        
        self.Help = wx.Menu()
        self.HelpHelp = wx.MenuItem(self.Help, -1, "Help...\tCtrl+H", "", wx.ITEM_NORMAL)
        self.HelpAbout = wx.MenuItem(self.Help, -1, "About", "", wx.ITEM_NORMAL)
        self.Help.AppendItem(self.HelpHelp)
        self.Help.AppendItem(self.HelpAbout)
        self.MenuBar.Append(self.Help, "Help")
        self.Bind(wx.EVT_MENU, lambda event: self.Destroy(), self.HelpHelp)
        self.Bind(wx.EVT_MENU, self.OnAbout, self.HelpAbout)
        
        #Actually set it
        self.SetMenuBar(self.MenuBar)

class MySplashScreen(wx.SplashScreen):
    """
    Create a splash screen widget.
    """
    def __init__(self, parent=None):
        # This is a recipe to a the screen.
        # Modify the following variables as necessary.
        img = wx.Image(name = os.path.join("imgs","PDSim_logo.png"))
        width, height = img.GetWidth(), img.GetHeight()
        width *= 0.5
        height *= 0.5
        aBitmap = img.Rescale(width,height).ConvertToBitmap()
        splashStyle = wx.SPLASH_CENTRE_ON_SCREEN | wx.SPLASH_TIMEOUT
        splashDuration = 2000 # milliseconds
        # Call the constructor with the above arguments in exactly the
        # following order.
        wx.SplashScreen.__init__(self, aBitmap, splashStyle,
                                 splashDuration, parent)
        self.Bind(wx.EVT_CLOSE, self.OnExit)

        wx.Yield()

    def OnExit(self, evt):
        self.Hide()
        evt.Skip()  # Make sure the default handler runs too...
                    
if __name__ == '__main__':
    # The following line is required to allow cx_Freeze 
    # to package multiprocessing properly.  Must be the first line 
    # after if __name__ == '__main__':
    freeze_support()
    
    app = wx.App(False)
    wx.InitAllImageHandlers()
    
    if '--nosplash' not in sys.argv:
        Splash=MySplashScreen()
        Splash.Show()
        time.sleep(2.0)
    
    frame = MainFrame() 
    frame.Show(True) 
    app.MainLoop()