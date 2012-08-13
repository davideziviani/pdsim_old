# -*- coding: latin-1 -*-

import wx
from wx.lib.mixins.listctrl import CheckListCtrlMixin
import CoolProp
from CoolProp.State import State
from CoolProp import CoolProp as CP
from ConfigParser import SafeConfigParser
import codecs
import numpy as np
import os
import itertools
import matplotlib as mpl
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as WXCanvas
from matplotlib.backends.backend_wxagg import NavigationToolbar2Wx as WXToolbar
from multiprocessing import Process
from PDSim.scroll import scroll_geo

class PlotPanel(wx.Panel):
    def __init__(self, parent, **kwargs):
        wx.Panel.__init__(self, parent, size = (300,200), **kwargs)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.figure = mpl.figure.Figure(dpi=100, figsize=(2, 2))
#        self.figure.set_figwidth(2.0)
#        self.figure.set_figheight(2.0)
        self.canvas = WXCanvas(self, -1, self.figure)
#        self.canvas.resize(200,200)
        self.toolbar = WXToolbar(self.canvas)
        self.toolbar.Realize()
        sizer.Add(self.canvas)
        sizer.Add(self.toolbar)
        self.SetSizer(sizer)
        sizer.Layout()

class PDPanel(wx.Panel):
    """
    A base class for panel with some goodies thrown in 
    
    Not intended for direct instantiation, rather it should be 
    subclassed, like in :class:`recip_panels.GeometryPanel`
    
    Loading from configuration file
    -------------------------------
    Method (A)
    
    Any subclassed PDPanel must either store a list of items as 
    ``self.items`` where the entries are dictionaries with at least the entries:
    
    - ``text``: The text description of the term 
    - ``attr``: The attribute in the simulation class that is linked with this term
    
    Other terms that can be included in the item are:
    - ``tooltip``: The tooltip to be attached to the textbox
    
    Method (B)
    
    The subclassed panel can provide the functions post_prep_for_configfile
    and post_get_from_configfile, each of which take no inputs.
    
    In post_prep_for_configfile, the subclassed panel can package up its elements
    into a form that can then be re-created when post_get_from_configfile is
    called
    
    
    Saving to configuration file
    ----------------------------
    
    Adding terms to parametric table
    --------------------------------
    
    """
    def __init__(self,*args,**kwargs):
        wx.Panel.__init__(self,*args,**kwargs)
        self.name=kwargs.get('name','')
        
    def _get_value(self,thing):
        #This first should work for wx.TextCtrl
        if hasattr(thing,'GetValue'):
            value=str(thing.GetValue()).strip()
            try:
                return float(value)
            except ValueError:
                return value
        elif hasattr(thing,'GetSelectionString'):
            value=thing.GetSelection()
            try:
                return float(value)
            except ValueError:
                return value
             
    def set_params(self, sim):
        
        if not hasattr(self,'items'):
            return
        else:
            items = self.items
        
        if hasattr(self,'skip_list'):
            # Don't actually set these attributes (they might over-write 
            # methods or attributes in the simulation)
            items = [item for item in items if item['attr'] not in self.skip_list()]
            
        for item in items:
            setattr(sim, item['attr'],self._get_value(item['textbox']))
    
    def ConstructItems(self,items,sizer,configdict=None,descdict=None):
        for item in items:
            #item is a dictionary of values including the keys:
            #  - attr
            #  - textbox
            #  - val
            
            if 'val' not in item and configdict is not None:
                k = item['attr']
                if k not in configdict:
                    self.warn_unmatched_attr(k)
                    val,item['text']=self.get_from_configfile(self.name, k, default = True)
                    print val, item['text']
                else:
                    val = configdict[k]
                    item['text'] = descdict[k]
            else:
                val = item['val']
                item['text'] = descdict[k]
                
            label=wx.StaticText(self, -1, item['text'])
            sizer.Add(label, 1, wx.EXPAND)
            textbox=wx.TextCtrl(self,-1,str(val))
            sizer.Add(textbox, 1, wx.EXPAND)
            item.update(dict(textbox=textbox,label=label))
        
    def warn_unmatched_attr(self, attr):
        print "didn't match attribute", attr
        
    def prep_for_configfile(self):
        """
        Writes the panel to a format ready for writing to config file
        using the entries in ``self.items``.  
        
        If there are other fields that need to get saved to file, the panel 
        can provide the ``post_prep_for_configfile`` function and add the additional fields 
        
        This function will call the ``post_prep_for_configfile`` if the subclass has it
        and add to the returned string
        """
        if self.name=='':
            return ''
            
        if not hasattr(self,'items'):
            self.items=[]
        
        s='['+self.name+']\n'
        
        for item in self.items:
            val = item['textbox'].GetValue()
            # Description goes into the StaticText control
            try:
                int(val)
                type_='int'
            except ValueError:
                try: 
                    float(val)
                    type_='float'
                except ValueError:
                    type_='string'
            s+=item['attr']+' = '+type_+','+item['textbox'].GetValue().encode('latin-1')+','+item['text']+'\n'
            
        if hasattr(self,'post_prep_for_configfile'):
            s+=self.post_prep_for_configfile()
        
        s=s.replace('%','%%')
        return s
           
    def _get_from_configfile(self, name, value):
        
        #Split at the first comma to get type, and value+description
        type,val_desc = value.split(',',1)
        #If it has a description, use it, otherwise, just use the config file key
        if len(val_desc.split(','))==2:
            val,desc_=val_desc.split(',')
            desc=desc_.strip()
        else:
            val=val_desc
            desc=name.strip()
            
        if type=='int':
            d=int(val)
        elif type=='float':
            d=float(val)
        elif type=='str':
            d=unicode(val)
        elif type=='State':
            Fluid,T,rho=val.split(',')
            d=dict(Fluid=Fluid,T=float(T),rho=float(rho))
        else:
            #Try to let the panel use the (name, value) directly
            if hasattr(self,'post_get_from_configfile'):
                d = self.post_get_from_configfile(name, value)
            else:
                raise KeyError('Type in line '+name+' = ' +value+' must be one of int,float,str')     
        return d, desc 
               
    def get_from_configfile(self, section, key = None, default = False):
        """
        configfile: file path or readable object (StringIO instance or file)
        Returns a dictionary with each of the elements from the given section 
        name from the given configuration file.  Each of the values in the configuration 
        file may have a string in the format 
        
        int,3,
        float,3.0
        string,hello
        
        so that the code can know what type the value is.  If the value is something else,
        ask post_get_from_configfile if it knows what to do with it
        
        """
        d, desc={}, {}
        
        Main = wx.GetTopLevelParent(self)
        parser, default_parser = Main.get_config_objects()
        
        #Section not included, use the default section from the default config file
        if not parser.has_section(section):
            dlg = wx.MessageDialog(None,'Section '+section+' was not found, falling back to default configuration file')
            dlg.ShowModal()
            dlg.Destroy()
            _parser = default_parser
        elif default:
            # We are programatically using the default parameters, 
            # don't warn automatically'
            _parser = default_parser
        else:
            _parser = parser
        
        if key is not None and default:
            value = _parser.get(section, key)
            _d,_desc =  self._get_from_configfile(key, value)
            return _d,_desc
        
        for name, value in _parser.items(section):
            _d,_desc = self._get_from_configfile(name,value)
            d[name] = _d
            desc[name] = _desc
            
        return d,desc
    
class ChangeParamsDialog(wx.Dialog):
    def __init__(self, params, **kwargs):
        wx.Dialog.__init__(self, None, **kwargs)
    
        self.params = params
        sizer = wx.FlexGridSizer(cols = 2)
        self.labels = []
        self.values = []
        self.attrs = []
    
        for p in self.params:
            l, v = LabeledItem(self,
                               label = p['desc'],
                               value = str(p['value'])
                               )
            self.labels.append(l)
            self.values.append(v)
            self.attrs.append(p['attr'])
            
            sizer.AddMany([l,v])
            
        self.SetSizer(sizer)
        min_width = min([l.GetSize()[0] for l in self.labels])
        for l in self.labels:
            l.SetMinSize((min_width,-1))
        sizer.Layout()
        self.Fit()
        
         #Bind a key-press event to all objects to get Esc 
        children = self.GetChildren()
        for child in children:
            child.Bind(wx.EVT_KEY_UP,  self.OnKeyPress)
    
    def get_values(self):
        params = []
        for l,v,k in zip(self.labels, self.values, self.attrs):
            params += [dict(desc = l.GetLabel(),
                           attr = k,
                           value = float(v.GetValue())
                        )]
        return params
            
    def OnAccept(self, event = None):
        self.EndModal(wx.ID_OK)
        
    def OnKeyPress(self,event = None):
        """ cancel if Escape key is pressed or accept if Enter """
        event.Skip()
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        elif event.GetKeyCode() == wx.WXK_RETURN:
            self.EndModal(wx.ID_OK)
    
    def CancelValues(self, event = None):
        self.EndModal(wx.ID_CANCEL)
            
class MassFlowOption(wx.Panel):
    def __init__(self,
                 parent,
                 key1, 
                 key2,
                 label = 'NONE',
                 types = None,
                 ):
        """
        A wx.Panel for selecting the flow model and associated parameters
        
        Should not be instantiated directly, rather subclassed in order to provide the list of dictionaries
        of flow models for a given type of machine
        """
        wx.Panel.__init__(self, parent)
        
        self.key1 = key1
        self.key2 = key2
        
        options = self.model_options()
        
        self.label = wx.StaticText(self, label=label)
        self.choices = wx.ComboBox(self)
        
        for option in options:
            self.choices.Append(option['desc'])
        self.choices.SetSelection(0)
        self.choices.SetEditable(False)
        self.options_list = options
        
        self.params = wx.Button(self, label='Params...')
        if not 'params' in option or not option['params']:
            self.params.Enable(False)
            
        else:
            TTS = self.dict_to_tooltip_string(option['params'])
            self.params.SetToolTipString(TTS)
            self.params_dict = option['params']
            
            self.params.Bind(wx.EVT_BUTTON, self.OnChangeParams)
        
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.label)
        sizer.Add(self.choices)
        sizer.Add(self.params)
        self.SetSizer(sizer)
        sizer.Layout()
    
    def OnChangeParams(self, event):
        """
        Open a dialog to change the values
        """
        dlg = ChangeParamsDialog(self.params_dict)
        if dlg.ShowModal() == wx.ID_OK:
            self.params_dict = dlg.get_values()
            TTS = self.dict_to_tooltip_string(self.params_dict)
            self.params.SetToolTipString(TTS)
        dlg.Destroy()
        
    def dict_to_tooltip_string(self, params):
        s = ''
        for param in params:
            s += param['desc'] + ': ' + str(param['value']) + '\n'
        return s
    
    def get_function_name(self):
        for option in self.options_list:
            if option['desc'] == self.choices.GetStringSelection():
                return option['function_name']
        raise AttributeError
        
    def model_options(self):
        """
        This function should return a list of dictionaries.  
        In each dictionary, the following terms must be defined:
        
        * desc : string
            Very terse description of the term 
        * function_name : function
            the function in the main machine class to be called
        * params : list of dictionaries
        
        MUST be implemented in the sub-class
        """
        raise NotImplementedError
    
class ParaSelectDialog(wx.Dialog):
    def __init__(self):
        wx.Dialog.__init__(self, None, title = "State Chooser",)
        self.MinLabel, self.Min = LabeledItem(self, label = 'Minimum value', value = '')
        self.MaxLabel, self.Max = LabeledItem(self, label = 'Maximum value', value = '')
        self.StepsLabel, self.Steps = LabeledItem(self, label = 'Number of steps', value = '')
        self.Accept = wx.Button(self, label = "Accept")
        sizer = wx.FlexGridSizer(cols = 2, hgap = 4, vgap = 4)
        sizer.AddMany([self.MinLabel, self.Min, self.MaxLabel, 
                       self.Max, self.StepsLabel, self.Steps, self.Accept])
        self.SetSizer(sizer)
        sizer.Layout()
        self.Fit()
        self.Accept.Bind(wx.EVT_BUTTON, self.OnAccept)
        
        #Bind a key-press event to all objects to get Esc 
        children = self.GetChildren()
        for child in children:
            child.Bind(wx.EVT_KEY_UP,  self.OnKeyPress)
        
    def join_values(self):
        values = np.linspace(float(self.Min.GetValue()),float(self.Max.GetValue()),int(self.Steps.GetValue()))
        return ', '.join([str(val) for val in values]) 
        
    def OnAccept(self, event = None):
        self.EndModal(wx.ID_OK)
        
    def OnKeyPress(self,event = None):
        """ cancel if Escape key is pressed """
        event.Skip()
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
    
    def CancelValues(self, event = None):
        self.EndModal(wx.ID_CANCEL)

class ParametricOption(wx.Panel):
    def __init__(self, parent, items):
        wx.Panel.__init__(self, parent)
        
        attrs = [item['attr'] for item in items]
        labels = [item['text'] for item in items]
        self.Terms = wx.ComboBox(self)
        self.Terms.AppendItems(labels)
        self.Terms.SetSelection(0)
        self.Terms.SetEditable(False)
        self.RemoveButton = wx.Button(self, label = '-', style = wx.ID_REMOVE)
        self.Values = wx.TextCtrl(self, value = '1,2,3,4,5,6,7,8,9')
        self.Select = wx.Button(self, label = 'Select...')
        
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.RemoveButton)
        sizer.Add(self.Terms)
        sizer.Add(self.Values)
        sizer.Add(self.Select)
        self.SetSizer(sizer)
        self.Select.Bind(wx.EVT_BUTTON,self.OnSelectValues)
        self.RemoveButton.Bind(wx.EVT_BUTTON, lambda event: self.Parent.RemoveTerm(self))
    
    def OnSelectValues(self, event = None):
        dlg = ParaSelectDialog()
        if dlg.ShowModal() == wx.ID_OK:
            self.Values.SetValue(dlg.join_values())
        dlg.Destroy()
        
    def get_values(self):
        name = self.Terms.GetStringSelection()
        #To list of floats
        values = [float(val) for val in self.Values.GetValue().split(',')]
        return name, values
    
    def set_values(self,key,value):
        self.Terms.SetStringSelection(key)
        self.Values.SetValue(value)
       
class ParametricCheckList(wx.ListCtrl, CheckListCtrlMixin):
    def __init__(self, parent, headers, values):
        wx.ListCtrl.__init__(self, parent, -1, style=wx.LC_REPORT)
        CheckListCtrlMixin.__init__(self)
        
        #Build the headers
        self.InsertColumn(0, '')
        self.SetColumnWidth(0, wx.LIST_AUTOSIZE)
        for i, header in enumerate(headers):
            self.InsertColumn(i+1, header)
        
        self.data = [row for row in itertools.product(*values)]
        
        #Add the values one row at a time
        for i,row in enumerate(self.data):
            self.InsertStringItem(i,'')
            for j,val in enumerate(row):
                self.SetStringItem(i,j+1,str(val))
            self.CheckItem(i)
            
        for i in range(len(headers)):
            self.SetColumnWidth(i+1,wx.LIST_AUTOSIZE_USEHEADER)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.OnItemActivated)

    def OnItemActivated(self, event):
        self.ToggleItem(event.m_itemIndex)
    
    def GetStringItem(self,Irow,Icol):
        return self.data[Irow][Icol]
                                 
class ParametricPanel(PDPanel):
    def __init__(self, parent, configfile, items, **kwargs):
        PDPanel.__init__(self, parent, **kwargs)
        
        self.variables =  items
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.ButtonSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.AddButton = wx.Button(self, label = "Add Term", style = wx.ID_ADD)
        self.AddButton.Bind(wx.EVT_BUTTON, self.OnAddTerm)
        self.ButtonSizer.Add(self.AddButton)
        sizer.Add(self.ButtonSizer)
        self.SetSizer(sizer)
        sizer.Layout()
        self.NTerms = 0
        self.ParamSizer = None
        self.ParamListSizer = None
        
        #Has no self.items, so all processing done through post_get_from_configfile
        self.get_from_configfile('ParametricPanel')
        
    def OnAddTerm(self, event = None):
        if self.NTerms == 0:
            self.ParamSizerBox = wx.StaticBox(self, label = "Parametric Terms")
            self.ParamSizer = wx.StaticBoxSizer(self.ParamSizerBox, wx.VERTICAL)
            self.GetSizer().Add(self.ParamSizer)
            self.cmdParaBuild = wx.Button(self, label = "Build Table")
            self.cmdParaBuild.Bind(wx.EVT_BUTTON, self.OnBuildTable)
            self.ButtonSizer.Add(self.cmdParaBuild)
        option = ParametricOption(self, self.variables)
        self.ParamSizer.Add(option)
        self.ParamSizer.Layout()
        self.NTerms += 1
        self.GetSizer().Layout()
        self.Refresh()
    
    def RemoveTerm(self, term):
        term.Destroy()
        self.NTerms -= 1
        if self.NTerms == 0:
            self.cmdParaBuild.Destroy()
            self.GetSizer().Remove(self.ParamSizer)
            if self.ParaList is not None:
                self.ParaList.Destroy()
                self.GetSizer().Remove(self.ParamListSizer)
                self.ParamListSizer = None 
            self.RunButton.Destroy()
        else:
            self.ParamSizer.Layout()
        self.GetSizer().Layout()
        self.Refresh()
        
    def OnBuildTable(self, event=None):
        names = []
        values = []
        #make names a list of strings
        #make values a list of lists of values
        for param in self.ParamSizer.GetChildren():
            name, val = param.Window.get_values()
            names.append(name)
            values.append(val)
        
        #Build the list of parameters for the parametric study
        if self.ParamListSizer is None:
            #Build and add a sizer for the para values
            self.ParamListBox = wx.StaticBox(self, label = "Parametric Terms Ranges")
            self.ParamListSizer = wx.StaticBoxSizer(self.ParamListBox, wx.VERTICAL)
        else:
            self.ParaList.Destroy()
            self.GetSizer().Remove(self.ParamListSizer)
#            #Build and add a sizer for the para values
            self.ParamListBox = wx.StaticBox(self, label = "Parametric Runs")
            self.ParamListSizer = wx.StaticBoxSizer(self.ParamListBox, wx.VERTICAL)
            
        self.GetSizer().Add(self.ParamListSizer,1,wx.EXPAND)
        self.ParaList = ParametricCheckList(self,names,values)
            
        self.ParamListSizer.Add(self.ParaList,1,wx.EXPAND)
        self.ParaList.SetMinSize((400,-1))
        self.ParamListSizer.Layout()
        self.GetSizer().Layout()
        self.Refresh() 
        
        if not hasattr(self,'RunButton'):
            self.RunButton = wx.Button(self, label='Run Table')
            self.RunButton.Bind(wx.EVT_BUTTON, self.OnRunTable)
            self.ButtonSizer.Add(self.RunButton)
            self.ButtonSizer.Layout()

    def OnRunTable(self, event=None):
        """
        Actually runs the parametric table
        
        This event can only fire if the table is built
        """
        Main = self.GetTopLevelParent()
        sims=[]
        #Column index 1 is the list of parameters
        self.ParaList.GetColumn(1)
        for Irow in range(self.ParaList.GetItemCount()):
            if self.ParaList.IsChecked(Irow):
                
                #Build the recip or the scroll
                if Main.SimType == 'recip':
                    sim = Main.build_recip()
                elif Main.SimType == 'scroll':
                    sim = Main.build_scroll()
                else:
                    raise AttributeError
                    
                for Icol in range(self.ParaList.GetColumnCount()-1):
                    val = self.ParaList.GetStringItem(Irow, Icol)
                    Name = self.ParaList.GetColumn(Icol+1).Text
                    setattr(sim,self._get_attr(Name),float(val))
                    #Run the post_set_params for all the panels
                    Main.MTB.InputsTB.post_set_params(sim)
            #Add an index for the run so that it can be sorted properly
            sim.run_index = Irow + 1
            sims.append(sim)
        Main.run_batch(sims)
        
    def post_prep_for_configfile(self):
        """
        This panel's outputs for the save file
        """
        s = ''
        for i, param in enumerate(self.ParamSizer.GetChildren()):
            name, vals = param.Window.get_values()
            values = ';'.join([str(val) for val in vals])
            s += 'Term' + str(i+1) + ' = Term,' + name +',' + values + '\n'
        return s
    
    def post_get_from_configfile(self,key,value):
        #value is something like "Term1,Piston diameter [m],0.02;0.025"
        string_, value = value.split(',')[1:3]
        #value = Piston diameter [m],0.02;0.025
        #Add a new entry to the table
        self.OnAddTerm()
        I = len(self.ParamSizer.GetChildren())-1
        #Load the values into the variables in the list of variables
        self.ParamSizer.GetItem(I).Window.set_values(string_,value.replace(';',', '))
        
    def _get_attr(self, Name):
        """
        Returns the attribute name corresponding to the given name
        """
        for item in self.variables:
            if item['text'] == Name:
                return item['attr']
        raise KeyError
        

def LabeledItem(parent,id=-1, label='A label', value='0.0', enabled=True, tooltip = None):
    """
    A convenience function that returns a tuple of StaticText and TextCtrl 
    items with the necessary label and values set
    """
    label = wx.StaticText(parent,id,label)
    thing = wx.TextCtrl(parent,id,value)
    if enabled==False:
        thing.Disable()
    if tooltip is not None:
        if enabled:
            thing.SetToolTipString(tooltip)
        else:
            label.SetToolTipString(tooltip)
    return label,thing

class StateChooser(wx.Dialog):
    def __init__(self,Fluid,T,rho,parent=None,id=-1):
        wx.Dialog.__init__(self,parent,id,"State Chooser",size=(300,250))
        
        class StateChoices(wx.Choicebook):
            def __init__(self, parent, id=-1,):
                wx.Choicebook.__init__(self, parent, id)
                
                self.pageT_dTsh=wx.Panel(self)
                self.AddPage(self.pageT_dTsh,'Saturation Temperature and Superheat')
                self.Tsatlabel1, self.Tsat1 = LabeledItem(self.pageT_dTsh,label="Saturation Temperature [K]",value='290')
                self.DTshlabel1, self.DTsh1 = LabeledItem(self.pageT_dTsh,label="Superheat [K]",value='11.1')
                sizer=wx.FlexGridSizer(cols=2,hgap=3,vgap=3)
                sizer.AddMany([self.Tsatlabel1, self.Tsat1,self.DTshlabel1, self.DTsh1])
                self.pageT_dTsh.SetSizer(sizer)
                
                self.pageT_p=wx.Panel(self)
                self.AddPage(self.pageT_p,'Temperature and Absolute Pressure')
                self.Tlabel1, self.T1 = LabeledItem(self.pageT_p,label="Temperature [K]",value='300')
                self.plabel1, self.p1 = LabeledItem(self.pageT_p,label="Pressure [kPa]",value='300')
                sizer=wx.FlexGridSizer(cols=2,hgap=3,vgap=3)
                sizer.AddMany([self.Tlabel1, self.T1,self.plabel1, self.p1])
                self.pageT_p.SetSizer(sizer)
        
        sizer=wx.BoxSizer(wx.VERTICAL)
        self.Fluidslabel = wx.StaticText(self,-1,'Fluid: ')
        self.Fluids = wx.ComboBox(self,-1)
        self.Fluids.AppendItems(sorted(CoolProp.__fluids__))
        self.Fluids.SetEditable(False)
        self.Fluids.SetValue(Fluid)
        
        hs = wx.BoxSizer(wx.HORIZONTAL)
        hs.AddMany([self.Fluidslabel,self.Fluids])
        sizer.Add(hs)
        
        sizer.Add((5,5))
        
        self.SC=StateChoices(self)
        sizer.Add(self.SC,1,wx.EXPAND)                
        
        fgs= wx.FlexGridSizer(cols=2,hgap=3,vgap=3)
        self.Tlabel, self.T = LabeledItem(self,label="Temperature [K]",value='300',enabled=False)
        self.plabel, self.p = LabeledItem(self,label="Pressure [kPa]",value='300',enabled=False)
        self.rholabel, self.rho = LabeledItem(self,label="Density [kg/m�]",value='1',enabled=False)
        fgs.AddMany([self.Tlabel,self.T,self.plabel,self.p,self.rholabel,self.rho])
        sizer.Add(fgs)
        
        self.cmdAccept = wx.Button(self,-1,"Accept")
        sizer.Add(self.cmdAccept)
        
        self.SetSizer(sizer)
        self.Fluids.SetStringSelection(Fluid)
        
        if CP.Props(Fluid,"Ttriple") < T < CP.Props(Fluid,"Tcrit"):
            #Pressure from temperature and density
            p = CP.Props('P','T',T,'D',rho,Fluid)
            #Saturation temperature
            Tsat = CP.Props('T','P',p,'Q',1,Fluid)
            self.SC.Tsat1.SetValue(str(Tsat))
            self.SC.DTsh1.SetValue(str(T-Tsat))
            self.SC.T1.SetValue(str(T))
            self.SC.p1.SetValue(str(p))
            self.SC.SetSelection(0) ## The page of Tsat,DTsh
        else:
            #Pressure from temperature and density
            p = CP.Props('P','T',T,'D',rho,Fluid)
            self.SC.T1.SetValue(str(T))
            self.SC.p1.SetValue(str(p))
            self.SC.SetSelection(1) ## The page of Tsat,DTsh
        
        self.OnUpdateVals()
        
        self.SC.Tsat1.Bind(wx.EVT_KEY_UP,self.OnUpdateVals)
        self.SC.DTsh1.Bind(wx.EVT_KEY_UP,self.OnUpdateVals)
        self.SC.T1.Bind(wx.EVT_KEY_UP,self.OnUpdateVals)
        self.SC.p1.Bind(wx.EVT_KEY_UP,self.OnUpdateVals)
        
        self.Fluids.Bind(wx.EVT_COMBOBOX, self.OnFlushVals)
        self.Bind(wx.EVT_CLOSE,self.CancelValues)
        self.cmdAccept.Bind(wx.EVT_BUTTON,self.AcceptValues)
        
        #Bind a key-press event to all objects to get Esc 
        children = self.GetChildren()
        for child in children:
            child.Bind(wx.EVT_KEY_UP,  self.OnKeyPress)
        
    def OnFlushVals(self,event=None):
        """ Clear all the values"""
        self.SC.Tsat1.SetValue("")
        self.SC.DTsh1.SetValue("")
        self.SC.T1.SetValue("")
        self.SC.p1.SetValue("")
        self.T.SetValue("")
        self.p.SetValue("")
        self.rho.SetValue("")
        
    def OnKeyPress(self,event=None):
        """ cancel if Escape key is pressed """
        event.Skip()
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
    
    def CancelValues(self,event=None):
        self.EndModal(wx.ID_CANCEL)
        
    def AcceptValues(self,event=None):
        """ If the state is in the vapor phase somewhere, accept it and return """
        Fluid = str(self.Fluids.GetStringSelection())
        T=float(self.T.GetValue())
        p=float(self.p.GetValue())
        if CP.Phase(Fluid,T,p) not in ['Gas','Supercritical']:
            dlg = wx.MessageDialog(None, message = "The phase is not gas or supercritical, cannot accept this state",caption='Invalid state')
            dlg.ShowModal()
            dlg.Destroy()
            return
        self.EndModal(wx.ID_OK)
    
    def GetValues(self):
        Fluid=str(self.Fluids.GetStringSelection())
        T=float(self.T.GetValue())
        p=float(self.p.GetValue())
        rho=float(self.rho.GetValue())
        return Fluid,T,p,rho
        
    def OnUpdateVals(self,event=None):
        if event is not None:
            event.Skip()
            
        PageNum = self.SC.GetSelection()
        Fluid = str(self.Fluids.GetStringSelection())
        try:
            if PageNum == 0:
                #Sat temperature and superheat are given
                p=CP.Props('P','T',float(self.SC.Tsat1.GetValue()),'Q',1.0,Fluid)
                T=float(self.SC.Tsat1.GetValue())+float(self.SC.DTsh1.GetValue())
                rho=CP.Props('D','T',T,'P',p,Fluid)
            elif PageNum == 1:
                #Temperature and pressure are given
                T=float(self.SC.T1.GetValue())
                p=float(self.SC.p1.GetValue())
                rho=CP.Props('D','T',T,'P',p,Fluid)
            else:
                raise NotImplementedError
            
            self.T.SetValue(str(T))
            self.p.SetValue(str(p))
            self.rho.SetValue(str(rho))
        except ValueError:
            return

    
class StatePanel(wx.Panel):
    """
    This is a generic Panel that has the ability to select a state given by 
    Fluid, temperature and density by selecting the desired set of inputs in a
    dialog which can be Tsat and DTsh or T & p.
    """
    def __init__(self,parent,id=-1,Fluid='R404A',T=283.15,rho=5.74):
        wx.Panel.__init__(self,parent,id)
        
        p = CP.Props('P','T',T,'D',rho,str(Fluid))
        sizer=wx.FlexGridSizer(cols=2,hgap=4,vgap=4)
        self.Fluidlabel, self.Fluid = LabeledItem(self,label="Fluid",value=str(Fluid),enabled=False)
        self.Tlabel, self.T = LabeledItem(self,label="Temperature [K]",value=str(T),enabled=False)
        self.plabel, self.p = LabeledItem(self,label="Pressure [kPa]",value=str(p),enabled=False)
        self.rholabel, self.rho = LabeledItem(self,label="Density [kg/m�]",value=str(rho),enabled=False)
        sizer.AddMany([self.Fluidlabel, self.Fluid,self.Tlabel,self.T,self.plabel,self.p,self.rholabel,self.rho])
        self.calcbtn=wx.Button(self,-1,"Choose")
        sizer.Add(self.calcbtn)
        self.calcbtn.Bind(wx.EVT_BUTTON, self.UseChooser)
        self.SetSizer(sizer)
        
    def GetState(self):
        """
        returns a :class:`CoolProp.State.State` instance from the given values
        in the panel
        """
        Fluid = str(self.Fluid.GetValue())
        T = float(self.T.GetValue())
        rho = float(self.rho.GetValue())
        return State(Fluid,dict(T=T,D=rho))
    
    def UseChooser(self,event=None):
        """
        An event handler that runs the State Chooser dialog and sets the
        values back in the panel
        """
        #Values from the GUI
        Fluid = str(self.Fluid.GetValue())
        T = float(self.T.GetValue())
        rho = float(self.rho.GetValue())
        
        #Instantiate the chooser Dialog
        SCfrm=StateChooser(Fluid=Fluid,T=T,rho=rho)
        
        #If they clicked accept
        if wx.ID_OK == SCfrm.ShowModal():
            Fluid,T,p,rho=SCfrm.GetValues()
            #Set the GUI values
            self.Fluid.SetValue(str(Fluid))
            self.T.SetValue(str(T))
            self.p.SetValue(str(p))
            self.rho.SetValue(str(rho))
        SCfrm.Destroy()

class StateInputsPanel(PDPanel):
    
    def __init__(self, parent, configfile,**kwargs):
    
        PDPanel.__init__(self, parent,**kwargs)
        
        #Loads all the parameters from the config file (case-sensitive)
        self.configdict, self.descdict = self.get_from_configfile('StatePanel')
        
        self.items = [
                      dict(attr='omega')
                      ]
        
        box_sizer = wx.BoxSizer(wx.VERTICAL)
        
        sizer = wx.FlexGridSizer(cols=2, vgap=4, hgap=4)
        self.ConstructItems([self.items[0]],sizer,self.configdict,self.descdict)
        box_sizer.Add(sizer)
        
        box_sizer.Add(wx.StaticText(self,-1,"Suction State"))
        box_sizer.Add(wx.StaticLine(self, -1, (25, 50), (300,1)))    
            
        Fluid = self.configdict['inletState']['Fluid']
        T = self.configdict['inletState']['T']
        rho = self.configdict['inletState']['rho']
        self.SuctionState = StatePanel(self,Fluid=Fluid,T=T,rho=rho)
        box_sizer.Add(self.SuctionState)
        
        box_sizer.Add((20,20))
        box_sizer.Add(wx.StaticText(self,-1,"Discharge State"))
        box_sizer.Add(wx.StaticLine(self,-1,(25, 50), (300,1)))
        
        self.cmbDischarge = wx.ComboBox(self)
        self.cmbDischarge.AppendItems(['Discharge pressure [kPa]', 'Pressure ratio [-]'])
        self.cmbDischarge.SetStringSelection(self.Discharge_key)
        self.DischargeValue = wx.TextCtrl(self, value = self.Discharge_value)
        self.cmbDischarge.Bind(wx.EVT_COMBOBOX, self.OnChangeDischarge)
        
        sizer = wx.FlexGridSizer(cols = 2, vgap = 4, hgap = 4)
        sizer.AddMany([self.cmbDischarge, self.DischargeValue])
        box_sizer.Add(sizer)
        
        self.SetSizer(box_sizer)
        sizer.Layout()
        
    def OnChangeDischarge(self, event):
        p_suction = self.SuctionState.GetState().p

        if self.cmbDischarge.GetStringSelection() == 'Discharge pressure [kPa]':
            pratio = float(self.DischargeValue.GetValue())
            p = pratio*p_suction
            self.DischargeValue.SetValue(str(p))
        elif self.cmbDischarge.GetStringSelection() == 'Pressure ratio [-]':
            p_disc = float(self.DischargeValue.GetValue())
            pratio = p_disc/p_suction
            self.DischargeValue.SetValue(str(pratio))
        else:
            raise KeyError
        
    def post_get_from_configfile(self, key, value):
        Dummy, value, key = value.split(',')
        self.Discharge_key = key
        self.Discharge_value = str(value)
        
    def post_set_params(self, simulation):
        simulation.inletState = self.SuctionState.GetState()
        if self.cmbDischarge.GetStringSelection() == 'Discharge pressure [kPa]':
            simulation.discharge_pressure = float(self.DischargeValue.GetValue())
        elif self.cmbDischarge.GetStringSelection() == 'Pressure ratio [-]':
            p_suction = self.SuctionState.GetState().p
            p_ratio = float(self.DischargeValue.GetValue())
            simulation.discharge_pressure = p_ratio * p_suction
        
    def post_prep_for_configfile(self):
        """
        Write a string representation of the state
        """
        State_ = self.SuctionState.GetState()
        StateString = 'inletState = State,'+State_.Fluid+','+str(State_.T)+','+str(State_.rho)
        DischargeString = 'discharge = Discharge,'+str(self.DischargeValue.GetValue())+','+self.cmbDischarge.GetStringSelection()
        return StateString+'\n'+DischargeString+'\n'


class InjectionViewerDialog(wx.Dialog):
    def __init__(self, geo, phi):
        wx.Dialog.__init__(self, parent = None)
        
        PP = PlotPanel(self)
        PP.ax = PP.figure.add_axes((0,0,1,1))
        scroll_geo.plot_injection_ports(0,geo,phi,PP.ax)
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(PP)
        sizer.Layout()
        
class InjectionElementPanel(wx.Panel):
    """
    A panel with the injection values for one injection port
    """
    def __init__(self, parent):
        wx.Panel.__init__(self,parent)
        
        self.state = StatePanel(self)
        self.Llabel,self.Lval = LabeledItem(self, label='Length of injection line',value='1.0')
        self.IDlabel,self.IDval = LabeledItem(self, label='Inner diameter of injection line',value='0.01')
        self.philabel,self.phival = LabeledItem(self, label='Involute angle',value='3.14159')
        self.btn = wx.Button(self, label='View')
        self.btn.Bind(wx.EVT_BUTTON, self.OnView)
        
        sizer = wx.FlexGridSizer(cols = 3)
        sizer.AddMany([self.Llabel,self.Lval])
        sizer.AddSpacer(10)
        sizer.AddMany([self.IDlabel,self.IDval])
        sizer.AddSpacer(10)
        sizer.AddMany([self.philabel,self.phival])
        sizer.Add(self.btn)
        sizer.Add(self.state)
        sizer.Layout()
        
    def OnView(self, event):
        geo = self.GetTopLevelParent().MTB.InputsTB.panels[0].Scroll.geo
        dlg = InjectionViewerDialog(geo,float(self.phival.GetValue()))
        dlg.ShowModal()
        
class InjectionInputsPanel(PDPanel):
    """
    The container panel for all the injection ports and injection data 
    """ 
    def __init__(self, parent):
        PDPanel.__init__(self,parent)
        
        self.InjectionElement = InjectionElementPanel(self)
        
        sizer = wx.FlexGridSizer(cols = 2)
        sizer.Add(self.InjectionElement)
        sizer.Layout()