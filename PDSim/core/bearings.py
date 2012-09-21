import quantities as pq
from math import pi,sqrt,log
import numpy as np
from scipy.interpolate import interp1d

class struct: pass

def thrust_bearing(**kwargs):
    """
    Analysis for the journal bearing
    
    We only take keyword arguments to avoid problems with order of parameters
    Parameters must be specified by name!
    
    Parameters
    ----------
    mu : float
        Friction coefficient [-]
        
    V : float
        Contact velocity [m/s]
        
    N : float
        Thrust force [N]
    
    
    Notes
    -----
    To derive the speed of contact of the orbiting scroll
    
    .. math::
    
        x = {r_o}\cos \left( {{\phi _{ie}} - \\frac{\pi }{2} - \\theta } \\right)

    .. math::
    
        y = {r_o}\sin \left( {{\phi _{ie}} - \\frac{\pi }{2} - \\theta } \\right)
    
    .. math::
    
        \\frac{{dx}}{{d\\theta }} = {r_o}\sin \left( {{\phi _{ie}} - \\frac{\pi }{2} - \\theta } \\right)\omega 
    
    .. math::
    
        \\frac{{dy}}{{d\\theta }} =  - {r_o}\cos \left( {{\phi _{ie}} - \\frac{\pi }{2} - \\theta } \\right)\omega

    .. math::
    
        \left| v \\right| = \sqrt {{{\left( {\\frac{{dx}}{{d\\theta }}} \\right)}^2} + {{\left( {\\frac{{dy}}{{d\\theta }}} \\right)}^2}}  = {r_o}\omega 
    """
    #Friction coefficient
    mu = kwargs.pop('mu', None)
    V = kwargs.pop('V', None)
    N = kwargs.pop('N', None)
    
    Wdot_loss_thrust = mu * V * N
    return dict(mu = mu,
                V = V,
                Wdot_loss = Wdot_loss_thrust
                )

def journal_bearing(**kwargs):
    
    """
    The necessary calculations for a journal bearing
    
    We only take keyword arguments to avoid problems with order of parameters
    Parameters must be specified by name!
    
    Parameters
    ----------
    r_b : float
        Radius of journal [m]
    L : float
        Length of journal [m]
    omega : float
        Rotational speed [rad/s]
    eta_0 : float
        Viscosity of lubricant [Pa-s]
    W : float
        Applied load [N]
    c : float
        Bearing clearance [m]
    design : string or float
        Either one of ``'friction'`` (design for minimum friction) 
        ``'load'`` (design for maximum load) or a floating point 
        value in the range [0,1] that weights the minimum friction
        and maximum load parameters and 0 gives the friction solution.
        Hamrock recommends a value of 0.5 for general applications
    
    
    Notes
    -----
    One of ``design`` or ``c`` must be provided
    
    Based on the method presented by
    
        Raimondi, A. A., and Boyd, J. (1958) : A Solution for the Finite 
        Journal Bearing and Its Application to Analysis and Design-I, -II, 
        and -III. ASLE Trans., vol. 1, no. I, I- pp. 159-174; II- pp. 175-193; 
        III- pp. 194-209.
    
    And further analysis presented in 
    From Hancock
    
        In Fig. 11.2 a recommended operating eccentricity ratio, or minimum film
        thickness, is indicated as well as a preferred operating area. The left boundary of
        the shaded zone defines the optimum eccentricity ratio for a minimum coefficient
        of friction, and the right boundary the optimum eccentricity ratio for maximum
        load. The recommended operating eccentricity for general application is midway
        between these two boundaries.

    """

    L = kwargs['L']
    r_b = kwargs['r_b']
    W = kwargs['W']
    eta_0 = kwargs['eta_0']
    omega = kwargs['omega']
    c = kwargs.get('c',None)
    design = kwargs.get('design',None)
    
    L_over_D = L/(2*r_b)
    
    if design is not None and c is None: 
        
        #First based on L/D and the design type, select the hm_over_c
        if design == 'friction':
            hm_over_c = 0.032*L_over_D**2 + 0.32*L_over_D - 0.052
        elif design == 'load':
            hm_over_c = 0.188272*log(L_over_D) + 0.541167
        elif isinstance(design,float):
            hm_over_c_friction = 0.032*L_over_D**2 + 0.32*L_over_D - 0.052
            hm_over_c_load = 0.188272*log(L_over_D) + 0.541167
            hm_over_c = design*hm_over_c_load+(1-design)*hm_over_c_friction
           
        #Interpolate in the data from to obtain S and rb/c*f
        _hm_c = [0.9,0.8,0.6,0.4,0.2,0.1,0.03]
        _L_D = [1,0.5,0.25]
        _S = [[1.33,0.631,0.264,0.121,0.0446,0.0188,0.00474],[4.31,2.03,0.779,0.319,0.0923,0.0313,0.00609],[16.2,7.57,2.83,1.07,0.261,0.0736,0.0101]]
        _rb_c_f = [[26.4, 12.8, 5.79, 3.22, 1.7, 1.05, 0.514],[85.6, 40.9, 17, 8.1, 3.26, 1.6, 0.61], [322, 153, 61.1, 26.7, 8.8, 3.5, 0.922]]
    
        from scipy.interpolate import interp2d
        f = interp2d(x=_hm_c,
                     y=_L_D,
                     z=_S,
                     kind = 'linear')
        S = f(hm_over_c,L_over_D)[0]
        f = interp2d(x=_hm_c,
                     y=_L_D,
                     z=_rb_c_f,
                     kind = 'linear')
        rb_c_f = f(hm_over_c,L_over_D)[0]
        
        #r_b/c from bearing number
        rb_c = sqrt((S*pi*W)/(eta_0*omega*r_b*L))
    
    elif design is None and c is not None:
        #Explicit solution for the bearing number since gap width is known
        S = (eta_0*omega*r_b*L)/(pi*W)*(r_b/c)**2
        
        # At each D/L ratio (0,1,2,4), find the non-dimensional frictional factor
        # by one-dimensional interpolation in the data of Raymondi and Boyd, 1958
        
        # f_coeffs is a dictionary with keys of values of D/L
        # values are lists of S, f*r_b/c
        
        #Added 1e-8 entries at the end since curves go through 0 but log(0) is undefined
        f_coeffs = {}
        f_coeffs[0.0]=[[0.24,0.123,0.0626,0.0389,0.021,0.0115,1e-8],
                      [4.8,2.57,1.52,1.2,0.961,0.756,1e-8]]
        f_coeffs[1.0]=[[1.33,0.631,0.264,0.121,0.0446,0.0188,0.00474,1e-8],
                      [26.4,12.8,5.79,3.22,1.7,1.05,0.514,1e-8]]
        f_coeffs[2.0]=[[4.31,2.03,0.779,0.319,0.0923,0.0313,0.00609,1e-8],
                      [85.6,40.9,17,8.1,3.26,1.6,0.61,1e-8]]
        f_coeffs[4.0]=[[16.2,7.57,2.83,1.07,0.261,0.0736,0.0101,1e-8],
                       [322,153,61.1,26.7,8.8,3.5,0.922,1e-8]]
        
        #for each D/L, find the value of f*r_b/c by 1-D interpolation
        log10_f_rb_c_list = []
        D_over_L_list = []
        
        for k,v in f_coeffs.iteritems():
            # input vectors for interp1d must be increasing, so flip both vectors
            # in-place
            v[0].reverse()
            v[1].reverse()
            
            #Use the logarithm for interpolation - see Hamrock figure
            x = np.log10(v[0])
            y = np.log10(v[1])
            
            #1D interpolation for log10(S)
            log10_f_rb_c_list.append(interp1d(x, y)(np.log10(S)))
            
            D_over_L_list.append(k)
            
        
        
        
        
        #get log10(f*rb/c) by 1-D interpolation for D/L
        log10_f_rb_c = interp1d(D_over_L_list, log10_f_rb_c_list)(2*r_b/L)
        
        #Get f*rb/c
        rb_c_f = 10.0**log10_f_rb_c
        
        #r_b/c from function inputs
        rb_c = r_b/c
    
    #Friction factor after all that [-]
    f = rb_c_f/rb_c
    
    #Frictional losses [W]
    Wdot_loss = omega*r_b*f*W
    
#    print 'W',W
#    print 'r_b',r_b
#    print 'omega',omega
#    print 'eta_0',eta_0
#    print 'S',S
#    print 'D_over_L_list',D_over_L_list
#    print 'log10_f_rb_c_list',log10_f_rb_c_list
#    print 'rb*f/c',rb_c_f
#    print 'f',f
    
    return dict(
                S = S,
                f = f,
                c = r_b/rb_c,
                Wdot_loss = Wdot_loss
                )
                
if __name__ == '__main__':

    print 'The following is the output from example 11.1 in Hamrock'
    print journal_bearing(r_b = 0.02, 
                        L = 0.04, 
                        design = 'load', 
                        W = 2200, 
                        eta_0 = 0.17, 
                        omega = 3600/60.0*2*pi
                        )
    