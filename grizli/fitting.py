"""
fitting.py

Created by Gabriel Brammer on 2017-05-19.

"""
import os
import glob

from collections import OrderedDict

import numpy as np

import astropy.io.fits as pyfits

from . import utils
#from .model import BeamCutout
from .utils import GRISM_COLORS

# Minimum redshift where IGM is applied
IGM_MINZ = 4

PLINE = {'kernel': 'point', 'pixfrac': 0.2, 'pixscale': 0.1, 'size': 8, 'wcs': None}

# IGM from eazy-py
try:
    import eazy.igm
    IGM = eazy.igm.Inoue14()
except:
    IGM = None

def run_all(id, t0=None, t1=None, fwhm=1200, zr=[0.65, 1.6], dz=[0.004, 0.0002], fitter='nnls', group_name='grism', fit_stacks=True, prior=None, fcontam=0.2, pline=PLINE, mask_sn_limit=3, fit_only_beams=False, fit_beams=True, root='', fit_trace_shift=False, phot=None, verbose=True, scale_photometry=False, show_beams=True):
    """Run the full procedure
    
    1) Load MultiBeam and stack files 
    2) 
    
    fwhm=1200; zr=[0.65, 1.6]; dz=[0.004, 0.0002]; group_name='grism'; fit_stacks=True; prior=None; fcontam=0.2; mask_sn_limit=3; fit_beams=True; root=''
    
    """
    import glob
    import grizli.multifit
    from grizli.stack import StackFitter
    from grizli.multifit import MultiBeam
    
    mb_files = glob.glob('{0}*{1:05d}.beams.fits'.format(root, id))
    st_files = glob.glob('{0}*{1:05d}.stack.fits'.format(root, id))
    
    st = StackFitter(st_files, fit_stacks=fit_stacks, group_name=group_name, fcontam=fcontam)
    
    mb = MultiBeam(mb_files[0], fcontam=fcontam, group_name=group_name)
    if len(mb_files) > 1:
        for file in mb_files[1:]:
            mb.extend(MultiBeam(file, fcontam=fcontam, group_name=group_name))
        
    if fit_trace_shift:
        b = mb.beams[0]
        sn_lim = fit_trace_shift*1
        if (np.max((b.model/b.grism['ERR'])[b.fit_mask.reshape(b.sh)]) > sn_lim) | (sn_lim > 100):
            shift = mb.fit_trace_shift(tol=1.e-3, verbose=verbose)
        
            #shift = mb.fit_trace_shift(tol=1.e-3)
    
    if phot is not None:
        st.set_photometry(**phot)
        mb.set_photometry(**phot)
            
    if t0 is None:
        t0 = grizli.utils.load_templates(line_complexes=True, fsps_templates=True, fwhm=fwhm)
    
    if t1 is None:
        t1 = grizli.utils.load_templates(line_complexes=False, fsps_templates=True, fwhm=fwhm)
        
    # Fit on stacked spectra
    if fit_only_beams:
        fit_obj = mb
    else:
        fit_obj = st
        
    fit = fit_obj.xfit_redshift(templates=t0, zr=zr, dz=dz, prior=prior, fitter=fitter, verbose=verbose) 
    fit_hdu = pyfits.table_to_hdu(fit)
    fit_hdu.header['EXTNAME'] = 'ZFIT_STACK'
    
    if scale_photometry:
        scl = mb.scale_to_photometry(z=fit.meta['z_map'][0], method='Powell', templates=t0, order=scale_photometry*1)
        if scl.status == 0:
            mb.pscale = scl.x
            st.pscale = scl.x
            
            fit = fit_obj.xfit_redshift(templates=t0, zr=zr, dz=dz, prior=prior, fitter=fitter, verbose=verbose) 
            fit_hdu = pyfits.table_to_hdu(fit)
            fit_hdu.header['EXTNAME'] = 'ZFIT_STACK'
            
        
    # Zoom-in fit with individual beams
    if fit_beams:
        #z0 = fit.meta['Z50'][0]
        z0 = fit.meta['z_map'][0]
        
        #width = np.maximum(3*fit.meta['ZWIDTH1'][0], 3*0.001*(1+z0))
        width = 20*0.001*(1+z0)
        
        mb_zr = z0 + width*np.array([-1,1])
        mb_fit = mb.xfit_redshift(templates=t0, zr=mb_zr, dz=[0.001, 0.0002], prior=prior, fitter=fitter, verbose=verbose) 
        mb_fit_hdu = pyfits.table_to_hdu(mb_fit)
        mb_fit_hdu.header['EXTNAME'] = 'ZFIT_BEAM'
    else:
        mb_fit = fit
           
    #### Get best-fit template 
    tfit = mb.template_at_z(z=mb_fit.meta['z_map'][0], templates=t1, fit_background=True, fitter=fitter)
    
    # Redrizzle? ... testing
    if False:
        hdu, fig = mb.drizzle_grisms_and_PAs(fcontam=fcontam,
                                         flambda=False,
                                         size=48, scale=1., 
                                         kernel='point', pixfrac=0.1,
                                         zfit=tfit)
                
    # Fit covariance
    cov_hdu = pyfits.ImageHDU(data=tfit['covar'], name='COVAR')
    Next = mb_fit.meta['N']
    cov_hdu.header['N'] = Next
    
    # Line EWs & fluxes
    coeffs_clip = tfit['coeffs'][mb.N:]
    covar_clip = tfit['covar'][mb.N:,mb.N:]
    lineEW = utils.compute_equivalent_widths(t1, coeffs_clip, covar_clip, max_R=5000, Ndraw=1000)
    
    for ik, key in enumerate(lineEW):
        cov_hdu.header['FLUX_{0:03d}'.format(ik)] = tfit['cfit'][key][0], '{0} line flux; erg / (s cm2)'.format(key.strip('line '))
        cov_hdu.header['ERR_{0:03d}'.format(ik)] = tfit['cfit'][key][1], '{0} line uncertainty; erg / (s cm2)'.format(key.strip('line '))
        
        cov_hdu.header['EW16_{0:03d}'.format(ik)] = lineEW[key][0], 'Rest-frame {0} EW, 16th percentile; Angstrom'.format(key.strip('line '))
        cov_hdu.header['EW50_{0:03d}'.format(ik)] = lineEW[key][1], 'Rest-frame {0} EW, 50th percentile; Angstrom'.format(key.strip('line '))
        cov_hdu.header['EW84_{0:03d}'.format(ik)] = lineEW[key][2], 'Rest-frame {0} EW, 84th percentile; Angstrom'.format(key.strip('line '))
        cov_hdu.header['EWHW_{0:03d}'.format(ik)] = (lineEW[key][2]-lineEW[key][0])/2, 'Rest-frame {0} EW, 1-sigma half-width; Angstrom'.format(key.strip('line '))
        
    # Best-fit template itself
    tfit_sp = grizli.utils.GTable()
    for ik, key in enumerate(tfit['cfit']):
        for save in [tfit_sp.meta]:
            save['CVAL{0:03d}'.format(ik)] = tfit['cfit'][key][0], 'Coefficient for {0}'.format(key)
            save['CERR{0:03d}'.format(ik)] = tfit['cfit'][key][1], 'Uncertainty for {0}'.format(key)
            save['CNAME{0:03d}'.format(ik)] = key, 'Template name'
                
    tfit_sp['wave'] = tfit['cont1d'].wave
    tfit_sp['continuum'] = tfit['cont1d'].flux
    tfit_sp['full'] = tfit['line1d'].flux
    
    tfit_sp['wave'].unit = tfit['cont1d'].waveunits
    tfit_sp['continuum'].unit = tfit['cont1d'].fluxunits
    tfit_sp['full'].unit = tfit['line1d'].fluxunits
    
    tfit_hdu = pyfits.table_to_hdu(tfit_sp)
    tfit_hdu.header['EXTNAME'] = 'TEMPL'
     
    # Make the plot
    fig = mb.xmake_fit_plot(mb_fit, tfit, show_beams=show_beams)
    
    # Add stack fit to the existing plot
    fig.axes[0].plot(fit['zgrid'], np.log10(fit['pdf']), color='0.5', alpha=0.5)
    fig.axes[0].set_xlim(fit['zgrid'].min(), fit['zgrid'].max())
    
    if phot is not None:
        fig.axes[1].errorbar(mb.photom_pivot/1.e4, mb.photom_flam/1.e-19, mb.photom_eflam/1.e-19, marker='s', alpha=0.5, color='k', linestyle='None')
    
    axc = fig.axes[1]
    oned_spec = mb.get_binned_spectra(coeffs=tfit['coeffs'])
    for g in oned_spec:
        
        pscale = 1.
        if hasattr(mb, 'pscale'):
            if (mb.pscale is not None):
                pscale = mb.compute_scale_array(mb.pscale, oned_spec[g]['wave'])
                
        axc.errorbar(oned_spec[g]['wave']/1.e4, oned_spec[g]['flux']/1.e-19/pscale, oned_spec[g]['err']/1.e-19/pscale, color=GRISM_COLORS[g], alpha=0.8, marker='.', linestyle='None', zorder=1)
          
    # if not fit_stacks:
    #     stx = StackFitter(st_files, fit_stacks=True, group_name=group_name, fcontam=fcontam)
    #     if phot is not None:
    #         stx.set_photometry(**phot)
    # else:
    #     stx = st
    # 
    # tfit_st = stx.template_at_z(z=mb_fit.meta['z_map'][0], templates=t1, fit_background=True)
    # 
    # axc = fig.axes[1]
    # for i in range(stx.N):
    #     beam = stx.beams[i]
    #     #m_i = beam.compute_model(spectrum_1d=sp, is_cgs=True, in_place=False).reshape(beam.sh)
    #     
    #     grism = beam.grism
    #     clean = beam.sci - beam.contam - tfit_st['cfit']['bg {0:03d}'.format(i)][0]
    #     w, fl, er = beam.optimal_extract(clean, ivar=beam.ivar)            
    #     #w, flm, erm = beam.optimal_extract(m_i, ivar=beam.ivar)
    #     
    #     sens = beam.sens
    #     
    #     # Some offset between drizzled and beam spectra, but can't shift
    #     # in the StackedSpectrum because redshift fit is about right
    #     w -= np.abs(w[1]-w[0])
    #     w = w/1.e4
    #          
    #     unit_corr = 1./sens
    #     
    #     # if 'DLAM0' in beam.header:
    #     #     unit_corr *= beam.header['DLAM']/beam.header['DLAM0']
    #         
    #     clip = (sens > 0.1*sens.max()) 
    #     clip &= (np.isfinite(fl)) & (er > 0)
    #     if clip.sum() == 0:
    #         continue
    #     
    #     fl *= unit_corr/1.e-19
    #     er *= unit_corr/1.e-19
    #     #flm *= unit_corr/1.e-19
    #     
    #     f_alpha = 0.8 #1./(stx.Ngrism[grism.upper()])*0.8 #**0.5
    #     
    #     # Plot
    #     axc.errorbar(w[clip], fl[clip], er[clip], color=GRISM_COLORS[grism], alpha=f_alpha, marker='.', linestyle='None', zorder=1)
    #     #axc.plot(w[clip], flm[clip], color='r', alpha=f_alpha, linewidth=2, zorder=10)
    #     
    #     if phot is not None:
    #         axc.errorbar(mb.photom_pivot/1.e4, mb.photom_flam/1.e-19, mb.photom_eflam/1.e-19, marker='.', linestyle='None', color='k', alpha=0.5)
            
    # Save the figure
    fig.savefig('{0}_{1:05d}.full.png'.format(group_name, id))
    
    # Make the line maps
    if pline is None:
         pzfit, pspec2, pline = grizli.multifit.get_redshift_fit_defaults()
    
    line_hdu = mb.drizzle_fit_lines(tfit, pline, force_line=['SIII','SII','Ha', 'OIII', 'Hb', 'OII'], save_fits=False, mask_lines=True, mask_sn_limit=mask_sn_limit)
    
    # Add beam exposure times
    exptime = mb.compute_exptime()
    for k in exptime:
        line_hdu[0].header['T_{0}'.format(k)] = (exptime[k], 'Total exposure time [s]')
         
    line_hdu.insert(1, fit_hdu)
    line_hdu.insert(2, cov_hdu)
    if fit_beams:
        line_hdu.insert(2, mb_fit_hdu)
    line_hdu.insert(3, tfit_hdu)
    
    line_hdu.writeto('{0}_{1:05d}.full.fits'.format(group_name, id), clobber=True, output_verify='fix')
    return mb, st, fit, tfit, line_hdu

def make_summary_catalog(target='pg0117+213', sextractor='pg0117+213-f140w.cat', include_beam=True):
    import glob
    import os
    import matplotlib.pyplot as plt
    
    import astropy.units as u
    import numpy as np
    import grizli
        
    keys = {0:['ID','RA','DEC','NINPUT','REDSHIFT','T_G141'],
            1:['CHI2POLY','DOF','CHIMIN','CHIMAX','BIC_POLY','BIC_TEMP','Z02', 'Z16', 'Z50', 'Z84', 'Z97', 'ZWIDTH1', 'ZWIDTH2', 'Z_MAP', 'Z_RISK', 'MIN_RISK'],
            2:['CHI2POLY','DOF','CHIMIN','CHIMAX','BIC_POLY','BIC_TEMP','Z02', 'Z16', 'Z50', 'Z84', 'Z97', 'ZWIDTH1', 'ZWIDTH2', 'Z_MAP', 'Z_RISK', 'MIN_RISK'],
            4:['FLUX_{0:03d} ERR_{0:03d} EW50_{0:03d} EWHW_{0:03d}'.format(i) for i in range(24)]}
    
    if not include_beam:
        keys[2] = keys[4]
        keys.pop(4)
        
    for k in keys:
        if k == 0:
            os.system('dfits {1}*full.fits | fitsort {2} | sed "s/\t/ , /g" > {1}.info.{0}'.format(k, target, ' '.join(keys[k])))
        else:
            os.system('dfits -x {0} {1}*full.fits | fitsort {2} |sed "s/FILE/FILE{0}/" | sed "s/\t/ , /g" > {1}.info.{0}'.format(k, target, ' '.join(keys[k])))
    
    for k in keys:
        if k == 0:
            info = grizli.utils.GTable.gread('{0}.info.{1}'.format(target, k), format='csv')
            info = info[info.colnames[:-1]]
        else:
            tab = grizli.utils.GTable.gread('{0}.info.{1}'.format(target, k), format='csv')
            tab = tab[tab.colnames[1:-1]]
            if (k == 2) & (include_beam):
                for c in tab.colnames:
                    tab.rename_column(c, 'BEAM_'+c)
                
            for c in tab.colnames:
                info.add_column(tab[c])
                
    for c in info.colnames:
        info.rename_column(c, c.lower())
    
    # Emission line names
    files=glob.glob('{0}*full.fits'.format(target))
    im = pyfits.open(files[0])
    h = im['COVAR'].header
    for i in range(24):
        line = h.comments['FLUX_{0:03d}'.format(i)].split()[0]
        for root in ['flux','err','ew50','ewhw']:
            col = '{0}_{1}'.format(root, line)
            info.rename_column('{0}_{1:03d}'.format(root, i), col)
            if root.startswith('ew'):
                info[col].format = '.1f'
            else:
                info[col].format = '.1e'
        
        info['sn_{0}'.format(line)] = info['flux_'+line]/info['err_'+line]
        info['sn_{0}'.format(line)][info['err_'+line] == 0] = -99
        info['sn_{0}'.format(line)].format = '.1f'
           
    info['chinu'] = info['chimin']/info['dof']
    info['chinu'].format = '.2f'
    
    info['bic_diff'] = info['bic_poly'] - info['bic_temp']
    info['bic_diff'].format = '.1f'
    
    info['log_risk'] = np.log10(info['min_risk'])
    info['log_risk'].format = '.2f'
    
    if include_beam:
        info['beam_chinu'] = info['beam_chimin']/info['beam_dof']
        info['beam_chinu'].format = '.2f'
    
        info['beam_bic_diff'] = info['beam_bic_poly'] - info['beam_bic_temp']
        info['beam_bic_diff'].format = '.1f'
    
        info['beam_log_risk'] = np.log10(info['beam_min_risk'])
        info['beam_log_risk'].format = '.2f'
        
    ### PNG columns    
    for ext in ['stack','full','line']:
        png = ['{0}_{1:05d}.{2}.png'.format(target, id, ext) for id in info['id']]
        info['png_{0}'.format(ext)] = ['<a href={0}><img src={0} height=200></a>'.format(p) for p in png]
    
    ### Column formats
    for col in info.colnames:
        if col.strip('beam_').startswith('z'):
            info[col].format = '.4f'
        
        if col in ['ra','dec']:
            info[col].format = '.6f'
            
    ### Sextractor catalog
    if sextractor is None:
        info.write('{0}.info.fits'.format(target), overwrite=True)
        return info
        
    #sextractor = glob.glob('{0}-f*cat'.format(target))[0]
    try:
        hcat = grizli.utils.GTable.gread(sextractor) #, format='ascii.sextractor')
    except:
        hcat = grizli.utils.GTable.gread(sextractor, sextractor=True)
    
    for c in hcat.colnames:
        hcat.rename_column(c, c.lower())
    
    idx, dr = hcat.match_to_catalog_sky(info, self_radec=('x_world', 'y_world'), other_radec=None)
    for c in hcat.colnames:
        info.add_column(hcat[c][idx])
        
    info.write('{0}.info.fits'.format(target), overwrite=True)
    return info
        
def _loss(dz, gamma=0.15):
    """Risk / Loss function, Tanaka et al. (https://arxiv.org/abs/1704.05988)
    
    Parameters
    ----------
    gamma : float
    
    Returns
    -------
    loss : float
    """
    return 1-1/(1+(dz/gamma)**2)
    
class GroupFitter(object):
    """Combine stack.StackFitter and MultiBeam fitting into a single object
    
    Will have to match the attributes between the different objects, which 
    is already close.
    """
    def _test(self):
        print(self.Ngrism)
    
    def _get_slices(self, masked=False):
        """Precompute array slices for how the individual components map into the single combined arrays.
        
        Parameters
        ----------
        masked : bool
            Return indices of masked arrays rather than simple slices of the 
            full beams.
        
        Returns
        -------
        slices : list
            List of slices.
        """
        x = 0
        slices = []
        # use masked index arrays rather than slices
        if masked:
            for i in range(self.N):
                beam = self.beams[i]
                if beam.fit_mask.sum() == 0:
                    slices.append(None)
                    continue
                    
                idx = np.arange(beam.fit_mask.sum())+x
                slices.append(idx) #[slice(x+0, x+beam.size)][beam.fit_mask])
                x = idx[-1]+1
        else:    
            for i in range(self.N):
                slices.append(slice(x+0, x+self.beams[i].size))
                x += self.beams[i].size
        
        return slices    
    
    def _update_beam_mask(self):
        """
        Compute versions of the masked arrays
        """
        for ib, b in enumerate(self.beams):
            b.fit_mask &= self.fit_mask[self.slices[ib]]
            
        self.mslices = self._get_slices(masked=True)
        self.Nmask = self.fit_mask.sum()       
        
    def _init_background(self, masked=True):
        """Initialize the (flat) background model components
        
        Parameters
        ----------
        None :
        
        Returns
        -------
        A_bg : `~np.ndarray`
            
        """
        if masked:
            A_bg = np.zeros((self.N, self.Nmask))
            for i in range(self.N):
                A_bg[i, self.mslices[i]] = 1.
        else:
            A_bg = np.zeros((self.N, self.Ntot))
            for i in range(self.N):
                A_bg[i, self.slices[i]] = 1. 
                           
        return A_bg
    
    def get_SDSS_photometry(self, bands='ugriz', templ=None, radius=2):
        from astroquery.sdss import SDSS
        from astropy import coordinates as coords
        import astropy.units as u
        import pysynphot as S
        
        from eazy.templates import Template
        from eazy.filters import FilterFile
        from eazy.photoz import TemplateGrid
        from eazy.filters import FilterDefinition
        
        pos = coords.SkyCoord(self.ra*u.deg, self.dec*u.deg, frame='icrs')
        fields = ['ra','dec','modelMag_r', 'modelMagErr_r']
        for b in bands:
            fields.extend(['modelFlux_'+b, 'modelFluxIvar_'+b])
            
        xid = SDSS.query_region(pos, photoobj_fields=fields, spectro=False, radius=radius*u.arcsec)
        
        if xid is None:
            return None
            
        filters = [FilterDefinition(bp=S.ObsBandpass('sdss,{0}'.format(b))) for b in bands]
        pivot = {}
        for ib, b in enumerate(bands):
            pivot[b] = filters[ib].pivot()
            
        to_flam = 10**(-0.4*(22.5+48.6))*3.e18 # / pivot(Ang)**2
        flam = np.array([xid['modelFlux_{0}'.format(b)][0]*to_flam/pivot[b]**2 for b in bands])
        eflam = np.array([np.sqrt(1/xid['modelFluxIvar_{0}'.format(b)][0])*to_flam/pivot[b]**2 for b in bands])
        
        phot = {'flam':flam, 'eflam':eflam, 'filters':filters, 'tempfilt':None}
        
        if templ is None:
            return phot
        
        # Make fast SDSS template grid
        templates = [Template(arrays=[templ[t].wave, templ[t].flux], name=t) for t in templ]
        zgrid = utils.log_zgrid(zr=[0.01, 3.4], dz=0.005)
        
        tempfilt = TemplateGrid(zgrid, templates, filters=filters, add_igm=True, galactic_ebv=0, Eb=0, n_proc=0)
        
        #filters = [all_filters.filters[f-1] for f in [156,157,158,159,160]]
        phot = {'flam':flam, 'eflam':eflam, 'filters':filters, 'tempfilt':tempfilt}
        return phot
        
    def set_photometry(self, flam=[], eflam=[], filters=[], force=False, tempfilt=None, min_err=0.02):
        """
        Add photometry
        """
        if (self.Nphot > 0) & (not force):
            print('Photometry already set (Nphot={0})'.format(self.Nphot))
            return True
        
        self.Nphot = (eflam > 0).sum() #len(flam)
        if self.Nphot == 0:
            return True
        
        if (len(flam) != len(eflam)) | (len(flam) != len(filters)):
            print('flam/eflam/filters dimensions don\'t match')
            return False
        
        self.photom_flam = flam
        self.photom_eflam = np.sqrt(eflam**2+(min_err*flam)**2)
        self.photom_eflam[eflam < 0] = -99
        
        self.photom_filters = filters
        
        self.sivarf = np.hstack((self.sivarf, 1/self.photom_eflam))
        self.weightf = np.hstack((self.weightf, self.photom_eflam*0+1))
        self.fit_mask = np.hstack((self.fit_mask, eflam > 0))
        self.Nmask = self.fit_mask.sum()       
        
        self.scif = np.hstack((self.scif, flam))
        
        self.DoF = int((self.weightf*self.fit_mask).sum())
        
        self.is_spec = np.isfinite(self.scif)
        self.is_spec[-len(flam):] = False
        
        self.photom_pivot = np.array([filter.pivot() for filter in filters])
        self.wavef = np.hstack((self.wavef, self.photom_pivot))
        
        # eazypy tempfilt for faster interpolation
        self.tempfilt = tempfilt
        
    def unset_photometry(self):
        if self.Nphot == 0:
            return True
            
        self.sivarf = self.sivarf[:-self.Nphot]
        self.weightf = self.weightf[:-self.Nphot]
        
        self.fit_mask = self.fit_mask[:-self.Nphot]
        self.scif = self.scif[:-self.Nphot]
        self.wavef = self.wavef[:-self.Nphot]
                
        self.Nmask = self.fit_mask.sum()
        self.DoF = int((self.weightf*self.fit_mask).sum())
        
        self.is_spec = 1
        self.Nphot = 0
        
    def _interpolate_photometry(self, z=0., templates=[]):
        """
        Interpolate templates through photometric filters
        
        xx: TBD better handling of emission line templates and use eazpy tempfilt
        object for huge speedup
        
        """
        NTEMP = len(templates)
        A_phot = np.zeros((NTEMP+self.N, len(self.photom_flam))) #self.Nphot))
        mask = self.photom_eflam > 0
        
        if (self.tempfilt is not None):
            if (self.tempfilt.NTEMP == NTEMP):
                #A_spl = self.tempfilt(z)
                A_phot[self.N:,:] = self.tempfilt(z)
                A_phot *= 3.e18/self.photom_pivot**2*(1+z)
                A_phot[~np.isfinite(A_phot)] = 0
                return A_phot[:,mask]

        for it, key in enumerate(templates):
            #print(key)
            tz = templates[key].zscale(z, scalar=1)
            for ifilt, filt in enumerate(self.photom_filters):
                A_phot[self.N+it, ifilt] = tz.integrate_filter(filt)*3.e18/self.photom_pivot[ifilt]**2#*(1+z)
            
            # pl = plt.plot(tz.wave, tz.flux)
            # plt.scatter(self.photom_pivot, A_phot[self.N+it,:], color=pl[0].get_color())
            
        return A_phot[:,mask]
        
    def xfit_at_z(self, z=0, templates=[], fitter='nnls', fit_background=True, get_uncertainties=False, get_design_matrix=False, pscale=None):
        """Fit the 2D spectra with a set of templates at a specified redshift.
        
        Parameters
        ----------
        z : float
            Redshift.
        
        templates : list
            List of templates to fit.
        
        fitter : str
            Minimization algorithm to compute template coefficients.
            The default 'nnls' uses non-negative least squares.  
            The other option is standard 'leastsq'.
        
        fit_background : bool
            Fit additive pedestal background offset.
            
        get_uncertainties : bool
            Compute coefficient uncertainties from the covariance matrix
        
        get_design_matrix : bool
            Return design matrix and data, rather than nominal outputs.
            
        Returns
        -------
        chi2 : float
            Chi-squared of the fit
        
        coeffs, coeffs_err : `~np.ndarray`
            Template coefficients and uncertainties.
        
        covariance : `~np.ndarray`
            Full covariance
            
        """
        import scipy.optimize
        import scipy.sparse
        
        NTEMP = len(templates)
        A = np.zeros((self.N+NTEMP, self.Nmask))
        if fit_background:
            A[:self.N,:self.Nmask-self.Nphot] = self.A_bgm
        
        lower_bound = np.zeros(self.N+NTEMP)
        lower_bound[:self.N] = -0.05
        upper_bound = np.ones(self.N+NTEMP)*np.inf
        upper_bound[:self.N] = 0.05
        
        # A = scipy.sparse.csr_matrix((self.N+NTEMP, self.Ntot))
        # bg_sp = scipy.sparse.csc_matrix(self.A_bg)
        
        COEFF_SCALE = 1.e-19
        
        for i, t in enumerate(templates):
            if t.startswith('line'):
                lower_bound[self.N+i] = -np.inf
                
            ti = templates[t]
            if z > IGM_MINZ:
                if IGM is None:
                    igmz = 1.
                else:
                    igmz = IGM.full_IGM(z, ti.wave*(1+z))         
            else:
                igmz = 1.
                
            s = [ti.wave*(1+z), ti.flux/(1+z)*igmz]
            
            for j, beam in enumerate(self.beams):
                mask_i = beam.fit_mask.reshape(beam.sh)
                clip = mask_i.sum(axis=0) > 0        
                if clip.sum() == 0:
                    continue
                                
                lam_beam = beam.wave[clip]
                if ((s[0].min() > lam_beam.max()) | 
                    (s[0].max() < lam_beam.min())):
                    continue

                sl = self.mslices[j]
                if t in beam.thumbs:
                    #print('Use thumbnail!', t)
                    A[self.N+i, sl] = beam.compute_model(thumb=beam.thumbs[t], spectrum_1d=s, in_place=False, is_cgs=True)[beam.fit_mask]*COEFF_SCALE
                else:
                    A[self.N+i, sl] = beam.compute_model(spectrum_1d=s, in_place=False, is_cgs=True)[beam.fit_mask]*COEFF_SCALE
                    
                # if j == 0:
                #     m = beam.compute_model(spectrum_1d=s, in_place=False, is_cgs=True)
                #     ds9.frame(i)
                #     ds9.view(m.reshape(beam.sh))
                        
        if fit_background:
            if fitter in ['nnls', 'lstsq']:
                pedestal = 0.04
            else:
                pedestal = 0.
        else:
            pedestal = 0
         
        # Photometry
        if self.Nphot > 0:
            A_phot = self._interpolate_photometry(z=z, templates=templates)
            A[:,-self.Nphot:] = A_phot*COEFF_SCALE #np.hstack((A, A_phot))
            
        #oktemp = (A*self.fit_mask).sum(axis=1) != 0
        oktemp = A.sum(axis=1) != 0
        
        # Weight design matrix and data by 1/sigma
        Ax = A[oktemp,:]*self.sivarf[self.fit_mask]        
        #AxT = Ax[:,self.fit_mask].T
        
        # Scale photometry
        if hasattr(self, 'pscale'):
            if (self.pscale is not None):
                scale = self.compute_scale_array(self.pscale, self.wavef[self.fit_mask]) 
                if self.Nphot > 0:
                    scale[-self.Nphot:] = 1.
                
                Ax *= scale
                if fit_background:
                    for i in range(self.N):
                        Ax[i,:] /= scale
        
        # Need transpose
        AxT = Ax.T
        
        # Masked data array, including background pedestal
        data = ((self.scif+pedestal*self.is_spec)*self.sivarf)[self.fit_mask]
        
        if get_design_matrix:
            return AxT, data
            
        # Run the minimization
        if fitter == 'nnls':
            coeffs_i, rnorm = scipy.optimize.nnls(AxT, data)            
        elif fitter == 'lstsq':
            coeffs_i, residuals, rank, s = np.linalg.lstsq(AxT, data)
        else:
            # Bounded Least Squares
            lsq_out = scipy.optimize.lsq_linear(AxT, data, bounds=(lower_bound[oktemp], upper_bound[oktemp]), method='bvls', tol=1.e-8)
            coeffs_i = lsq_out.x
            
        # Compute background array         
        if fit_background:
            background = np.dot(coeffs_i[:self.N], A[:self.N,:]) - pedestal
            if self.Nphot > 0:
                background[-self.Nphot:] = 0.
            coeffs_i[:self.N] -= pedestal
        else:
            background = self.scif[self.fit_mask]*0.
            
        # Full model
        if fit_background:
            model = np.dot(coeffs_i[self.N:], Ax[self.N:,:]/self.sivarf[self.fit_mask])
        else:
            model = np.dot(coeffs_i, Ax/self.sivarf[self.fit_mask])
            
        # Residuals and Chi-squared
        resid = self.scif[self.fit_mask] - model - background
        #chi2 = np.sum(resid[self.fit_mask]**2*self.sivarf[self.fit_mask]**2)
        chi2 = np.sum(resid**2*self.sivarf[self.fit_mask]**2*self.weightf[self.fit_mask])
        
        if self.Nphot > 0:
            self.photom_model = model[-self.Nphot:]*1
            
        # Uncertainties from covariance matrix
        if get_uncertainties:
            try:
                # Covariance is inverse of AT.A
                covar_i = np.matrix(np.dot(AxT.T, AxT)).I.A
                covar = utils.fill_masked_covar(covar_i, oktemp)
                covard = np.sqrt(covar.diagonal())
                
                # Compute covariances after masking templates with coeffs = 0
                if get_uncertainties == 2:
                    nonzero = coeffs_i != 0
                    if nonzero.sum() > 0:
                        AxTm = AxT[:,nonzero]
                        #mcoeffs_i, rnorm = scipy.optimize.nnls(AxTm, data)            
                        #mcoeffs_i[:self.N] -= pedestal

                        mcovar_i = np.matrix(np.dot(AxTm.T, AxTm)).I.A
                        mcovar = utils.fill_masked_covar(mcovar_i, nonzero)
                        mcovar = utils.fill_masked_covar(mcovar, oktemp)
                        mcovard = np.sqrt(mcovar.diagonal())
                        
                        covar = mcovar
                        covard = mcovard
            except:
                print('Except: covar!')
                covar = np.zeros((self.N+NTEMP, self.N+NTEMP))
                covard = np.zeros(self.N+NTEMP)#-1.
                mcovard = covard
        else:
            covar = np.zeros((self.N+NTEMP, self.N+NTEMP))
            covard = np.zeros(self.N+NTEMP)#-1.
            
        coeffs = np.zeros(self.N+NTEMP)
        coeffs[oktemp] = coeffs_i #[self.N:]] = coeffs[self.N:]

        coeffs_err = covard #np.zeros(NTEMP)
        #full_coeffs_err[oktemp[self.N:]] = covard[self.N:]
        del(A); del(Ax); del(AxT)
        
        #if fit_background:
        coeffs[self.N:] *= COEFF_SCALE
        coeffs_err[self.N:] *= COEFF_SCALE
        covar[self.N:,self.N:] *= COEFF_SCALE**2
            
        return chi2, coeffs, coeffs_err, covar
    
    def xfit_redshift(self, prior=None, fwhm=1200,
                     make_figure=True, zr=[0.65, 1.6], dz=[0.005, 0.0004],
                     verbose=True, fit_background=True, fitter='nnls', 
                     delta_chi2_threshold=0.004, poly_order=3, zoom=True, 
                     line_complexes=True, templates={}, figsize=[8,5],
                     fsps_templates=False, get_uncertainties=True):
        """TBD
        """
        from scipy import polyfit, polyval
        
        if zr is 0:
            stars = True
            zr = [0, 0.01]
            fitter='nnls'
        else:
            stars = False
            
        zgrid = utils.log_zgrid(zr, dz=dz[0])
        NZ = len(zgrid)
        
        #### Polynomial SED fit
        wpoly = np.linspace(1000,5.e4,1000)
        # tpoly = utils.polynomial_templates(wpoly, line=True)
        # out = self.xfit_at_z(z=0., templates=tpoly, fitter='nnls',
        #                     fit_background=True, get_uncertainties=False)
        tpoly = utils.polynomial_templates(wpoly, order=poly_order,
                                           line=False)
        out = self.xfit_at_z(z=0., templates=tpoly, fitter='lstsq',
                            fit_background=True, get_uncertainties=False)
        
        chi2_poly, coeffs_poly, err_poly, cov = out
        #poly1d, xxx = utils.dot_templates(coeffs_poly[self.N:], tpoly, z=0)

        # tpoly = utils.polynomial_templates(wpoly, order=3)
        # out = self.xfit_at_z(z=0., templates=tpoly, fitter='lstsq',
        #                     fit_background=True)          
        # chi2_poly, coeffs_poly, c, cov = out

        # if True:
        #     cp, lp = utils.dot_templates(coeffs_poly[self.N:], tpoly)
            
        ### Set up for template fit
        if templates == {}:
            templates = utils.load_templates(fwhm=fwhm, stars=stars, line_complexes=line_complexes, fsps_templates=fsps_templates)
        else:
            if verbose:
                print('User templates! N={0} \n'.format(len(templates)))
            
        NTEMP = len(templates)
        
        out = self.xfit_at_z(z=0., templates=templates, fitter=fitter,
                            fit_background=fit_background, 
                            get_uncertainties=get_uncertainties)
                            
        chi2, coeffs, coeffs_err, covar = out
        
        chi2 = np.zeros(NZ)
        coeffs = np.zeros((NZ, coeffs.shape[0]))
        covar = np.zeros((NZ, covar.shape[0], covar.shape[1]))
        
        chi2min = 1e30
        iz = 0
        for i in range(NZ):
            out = self.xfit_at_z(z=zgrid[i], templates=templates,
                                fitter=fitter, fit_background=fit_background,
                                get_uncertainties=get_uncertainties)
            
            chi2[i], coeffs[i,:], coeffs_err, covar[i,:,:] = out
            if chi2[i] < chi2min:
                iz = i
                chi2min = chi2[i]

            if verbose:                    
                print(utils.NO_NEWLINE + '  {0:.4f} {1:9.1f} ({2:.4f}) {3:d}/{4:d}'.format(zgrid[i], chi2[i], zgrid[iz], i+1, NZ))
        
        if verbose:
            print('First iteration: z_best={0:.4f}\n'.format(zgrid[iz]))
            
        ## Find peaks
        import peakutils
        
        # Make "negative" chi2 for peak-finding
        if chi2_poly > (chi2.min()+100):
            chi2_rev = (chi2.min() + 100 - chi2)/self.DoF
        elif chi2_poly < (chi2.min() + 9):
            chi2_rev = (chi2.min() + 16 - chi2)/self.DoF
        else:
            chi2_rev = (chi2_poly - chi2)/self.DoF
            
        chi2_rev[chi2_rev < 0] = 0
        indexes = peakutils.indexes(chi2_rev, thres=0.4, min_dist=8)
        num_peaks = len(indexes)
        
        if False:
            plt.plot(zgrid, (chi2-chi2.min())/ self.DoF)
            plt.scatter(zgrid[indexes], (chi2-chi2.min())[indexes]/ self.DoF, color='r')
        
        # delta_chi2 = (chi2.max()-chi2.min())/self.DoF
        # if delta_chi2 > delta_chi2_threshold:      
        if (num_peaks > 0) & (not stars) & zoom:
            zgrid_zoom = []
            for ix in indexes:
                if (ix > 0) & (ix < len(chi2)-1):
                    c = polyfit(zgrid[ix-1:ix+2], chi2[ix-1:ix+2], 2)
                    zi = -c[1]/(2*c[0])
                    chi_i = polyval(c, zi)
                    zgrid_zoom.extend(np.arange(zi-2*dz[0], 
                                      zi+2*dz[0]+dz[1]/10., dz[1]))
                    
            # zgrid_zoom = utils.zoom_zgrid(zgrid, chi2/self.DoF,
            #                               threshold=delta_chi2_threshold,
            #                               factor=dz[0]/dz[1])
            NZOOM = len(zgrid_zoom)
        
            chi2_zoom = np.zeros(NZOOM)
            coeffs_zoom = np.zeros((NZOOM, coeffs.shape[1]))
            covar_zoom = np.zeros((NZOOM, coeffs.shape[1], covar.shape[2]))

            iz = 0
            chi2min = 1.e30
            for i in range(NZOOM):
                out = self.xfit_at_z(z=zgrid_zoom[i], templates=templates,
                                    fitter=fitter,
                                    fit_background=fit_background,
                                    get_uncertainties=get_uncertainties)

                chi2_zoom[i], coeffs_zoom[i,:], e, covar_zoom[i,:,:] = out
                #A, coeffs_zoom[i,:], chi2_zoom[i], model_2d = out
                if chi2_zoom[i] < chi2min:
                    chi2min = chi2_zoom[i]
                    iz = i
                
                if verbose:
                    print(utils.NO_NEWLINE+'- {0:.4f} {1:9.1f} ({2:.4f}) {3:d}/{4:d}'.format(zgrid_zoom[i], chi2_zoom[i], zgrid_zoom[iz], i+1, NZOOM))
        
            zgrid = np.append(zgrid, zgrid_zoom)
            chi2 = np.append(chi2, chi2_zoom)
            coeffs = np.append(coeffs, coeffs_zoom, axis=0)
            covar = np.vstack((covar, covar_zoom))
            
        so = np.argsort(zgrid)
        zgrid = zgrid[so]
        chi2 = chi2[so]
        coeffs = coeffs[so,:]
        covar = covar[so,:,:]
        
        fit = utils.GTable()
        fit.meta['N'] = (self.N, 'Number of spectrum extensions')
        fit.meta['polyord'] = (poly_order, 'Order polynomial fit')
        fit.meta['chi2poly'] = (chi2_poly, 'Chi^2 of polynomial fit')
        fit.meta['DoF'] = (self.DoF, 'Degrees of freedom (number of pixels)')
        fit.meta['chimin'] = (chi2.min(), 'Minimum chi2')
        fit.meta['chimax'] = (chi2.max(), 'Maximum chi2')
        fit.meta['fitter'] = (fitter, 'Minimization algorithm')
        
        # Bayesian information criteria, normalized to template min_chi2
        # BIC = log(number of data points)*(number of params) + min(chi2) + C
        # https://en.wikipedia.org/wiki/Bayesian_information_criterion
        fit.meta['bic_poly'] = np.log(self.DoF)*(poly_order+1+self.N) + (chi2_poly-chi2.min()), 'BIC of polynomial fit'
        
        izbest = np.argmin(chi2)
        clip = coeffs[izbest,:] != 0
        fit.meta['bic_temp'] = np.log(self.DoF)*clip.sum(), 'BIC of template fit'
        
        fit.meta['NTEMP'] = (len(templates), 'Number of fitting templates')
        
        for i, tname in enumerate(templates):
            fit.meta['T{0:03d}NAME'.format(i+1)] = (templates[tname].name, 'Template name')
            if tname.startswith('line '):
                fit.meta['T{0:03d}FWHM'.format(i+1)] = (templates[tname].fwhm, 'FWHM, if emission line')
        
        dtype = np.float64
        
        fit['zgrid'] = np.cast[dtype](zgrid)
        fit['chi2'] = np.cast[dtype](chi2)
        #fit['chi2poly'] = chi2_poly
        fit['coeffs'] = np.cast[dtype](coeffs)
        fit['covar'] = np.cast[dtype](covar)
        
        fit = self._parse_zfit_output(fit, prior=prior)
        
        return fit
    
    def _parse_zfit_output(self, fit, prior=None):
        """Parse best-fit redshift, etc.
        TBD
        """
        import scipy.interpolate
        
        # Normalize to min(chi2)/DoF = 1.
        scl_nu = fit['chi2'].min()/self.DoF
        
        # PDF
        pdf = np.exp(-0.5*(fit['chi2']-fit['chi2'].min())/scl_nu)
        
        if prior is not None:
            interp_prior = np.interp(fit['zgrid'], prior[0], prior[1])
            pdf *= interp_prior
            fit.meta['hasprior'] = True, 'Prior applied to PDF'
            fit['prior'] = interp_prior
        else:
            interp_prior = None
            fit.meta['hasprior'] = False, 'Prior applied to PDF'

        # Normalize PDF
        pdf /= np.trapz(pdf, fit['zgrid'])
        
        # Interpolate pdf for more continuous measurement
        spl = scipy.interpolate.Akima1DInterpolator(fit['zgrid'], np.log(pdf), axis=1)
        zfine = utils.log_zgrid(zr=[fit['zgrid'].min(), fit['zgrid'].max()], dz=0.0001)
        ok = np.isfinite(spl(zfine))
        norm = np.trapz(np.exp(spl(zfine[ok])), zfine[ok])
        
        # Compute CDF and probability intervals
        dz = np.gradient(zfine[ok])
        cdf = np.cumsum(np.exp(spl(zfine[ok]))*dz/norm)
        pz_percentiles = np.interp(np.array([2.5, 16, 50, 84, 97.5])/100., cdf, zfine[ok])

        # Random draws, testing
        #rnd = np.interp(np.random.rand(1000), cdf, fit['zgrid']+dz/2.)
        
        dz = np.gradient(fit['zgrid'])
        
        gamma = 0.15
        zsq = np.dot(fit['zgrid'][:,None], np.ones_like(fit['zgrid'])[None,:])
        L = _loss((zsq-fit['zgrid'])/(1+fit['zgrid']), gamma=gamma)
        
        risk = np.dot(pdf*L, dz)
        zi = np.argmin(risk)
        
        #print('xxx', zi, len(risk))
        
        if (zi < len(risk)-1) & (zi > 0):
            c = np.polyfit(fit['zgrid'][zi-1:zi+2], risk[zi-1:zi+2], 2)
            z_risk = -c[1]/(2*c[0])
        else:
            z_risk = fit['zgrid'][zi]
            
        min_risk = np.trapz(pdf*_loss((z_risk-fit['zgrid'])/(1+fit['zgrid']), gamma=gamma), fit['zgrid'])
        
        # MAP, maximum p(z)
        zi = np.argmax(pdf)
        if (zi < len(pdf)-1) & (zi > 0):
            c = np.polyfit(fit['zgrid'][zi-1:zi+2], pdf[zi-1:zi+2], 2)
            z_map = -c[1]/(2*c[0])
        else:
            z_map = fit['zgrid'][zi]
            
        # Store data in the fit table
        fit['pdf'] = pdf
        fit['risk'] = risk
        fit.meta['Z02'] = pz_percentiles[0], 'Integrated p(z) = 0.025'
        fit.meta['Z16'] = pz_percentiles[1], 'Integrated p(z) = 0.16'
        fit.meta['Z50'] = pz_percentiles[2], 'Integrated p(z) = 0.5'
        fit.meta['Z84'] = pz_percentiles[3], 'Integrated p(z) = 0.84'
        fit.meta['Z97'] = pz_percentiles[4], 'Integrated p(z) = 0.975'
        fit.meta['ZWIDTH1'] = pz_percentiles[3]-pz_percentiles[1], 'Width between the 16th and 84th p(z) percentiles'
        fit.meta['ZWIDTH2'] = pz_percentiles[4]-pz_percentiles[0], 'Width between the 2.5th and 97.5th p(z) percentiles'
        
        fit.meta['z_map'] = z_map, 'Redshift at MAX(PDF)'
        
        fit.meta['z_risk'] = z_risk, 'Redshift at minimum risk'
        fit.meta['min_risk'] = min_risk, 'Minimum risk'
        fit.meta['gam_loss'] = gamma, 'Gamma factor of the risk/loss function'
        return fit
                        
    def template_at_z(self, z=0, templates=None, fit_background=True, fitter='nnls', fwhm=1400, get_uncertainties=2):
        """TBD
        """
        if templates is None:
            templates = utils.load_templates(line_complexes=False, fsps_templates=True, fwhm=fwhm)
        
        out = self.xfit_at_z(z=z, templates=templates, fitter=fitter, 
                             fit_background=fit_background,
                             get_uncertainties=get_uncertainties)

        chi2, coeffs, coeffs_err, covar = out
        cont1d, line1d = utils.dot_templates(coeffs[self.N:], templates, z=z)

        # Parse template coeffs
        cfit = OrderedDict()
        
        for i in range(self.N):
            cfit['bg {0:03d}'.format(i)] = coeffs[i], coeffs_err[i]
        
        for j, key in enumerate(templates):
            i = j+self.N
            cfit[key] = coeffs[i], coeffs_err[i]
        
        if False:
            # Compare drizzled and beam fits (very close)
            for j, key in enumerate(templates):
                print('{key:<16s} {0:.2e} {1:.2e}  {2:.2e} {3:.2e}'.format(mb_cfit[key][0], mb_cfit[key][1], st_cfit[key][0], st_cfit[key][1], key=key))
        
        tfit = OrderedDict()
        tfit['cont1d'] = cont1d
        tfit['line1d'] = line1d
        tfit['cfit'] = cfit
        tfit['coeffs'] = coeffs
        tfit['covar'] = covar
        tfit['z'] = z
        tfit['templates'] = templates
        
        return tfit #cont1d, line1d, cfit, covar
        
        ### Random draws
        # Unique wavelengths
        wfull = np.hstack([templates[key].wave for key in templates])
        w = np.unique(wfull)
        so = np.argsort(w)
        w = w[so]
        
        xclip = (w*(1+z) > 7000) & (w*(1+z) < 1.8e4)
        temp = np.array([grizli.utils_c.interp.interp_conserve_c(w[xclip], templates[key].wave, templates[key].flux) for key in templates])
        
        clip = coeffs_err[self.N:] > 0
        covar_clip = covar[self.N:,self.N:][clip,:][:,clip]
        draws = np.random.multivariate_normal(coeffs[self.N:][clip], covar_clip, size=100)
        
        tdraw = np.dot(draws, temp[clip,:])/(1+z)
            
        for ib, beam in enumerate(self.beams):
            ww, ff, ee = beam.optimal_extract(beam.sci - beam.contam - coeffs[ib])
            plt.errorbar(ww, ff/beam.sens, ee/beam.sens, color='k', marker='.', linestyle='None', alpha=0.5)
            
            for i in range(tdraw.shape[0]):
                sp = [w[xclip]*(1+z), tdraw[i,:]]
                m = beam.compute_model(spectrum_1d=sp, is_cgs=True, in_place=False).reshape(beam.sh)
                ww, ff, ee = beam.optimal_extract(m)
                plt.plot(ww, ff/beam.sens, color='r', alpha=0.05)
                
            plt.plot(w[xclip]*(1+z), tdraw.T, alpha=0.05, color='r')
    
    def xmake_fit_plot(self, fit, tfit, show_beams=True, bin=0, minor=0.1):
        """TBD
        """
        import matplotlib.pyplot as plt
        import matplotlib.gridspec
        from matplotlib.ticker import MultipleLocator
        
        import grizli.model
        
        # Initialize plot window
        Ng = len(self.grisms)
        gs = matplotlib.gridspec.GridSpec(1,2, 
                        width_ratios=[1,1.5+0.5*(Ng>1)],
                        hspace=0.)
            
        fig = plt.figure(figsize=[8+4*(Ng>1), 3.5])
        
        # p(z)
        axz = fig.add_subplot(gs[-1,0]) #121)
        
        axz.text(0.95, 0.96, self.group_name + '\n'+'ID={0:<5d}  z={1:.4f}'.format(self.id, fit.meta['z_risk'][0]), ha='right', va='top', transform=axz.transAxes, fontsize=9)
                 
        axz.plot(fit['zgrid'], np.log10(fit['pdf']), color='k')
        #axz.fill_between(z, (chi2-chi2.min())/scale_nu, 27, color='k', alpha=0.5)
        
        axz.set_xlabel(r'$z$')
        axz.set_ylabel(r'$\log\ p(z)$'+' / '+ r'$\chi^2=\frac{{{0:.0f}}}{{{1:d}}}={2:.2f}$'.format(fit.meta['chimin'][0], fit.meta['DoF'][0], fit.meta['chimin'][0]/fit.meta['DoF'][0]))
        #axz.set_yticks([1,4,9,16,25])
        
        axz.set_xlim(fit['zgrid'].min(), fit['zgrid'].max())
        pzmax = np.log10(fit['pdf'].max())
        axz.set_ylim(pzmax-6, pzmax+0.8)
        axz.grid()
        axz.yaxis.set_major_locator(MultipleLocator(base=1))
        
        #### Spectra
        axc = fig.add_subplot(gs[-1,1]) #224)
        ymin = 1.e30
        ymax = -1.e30
        wmin = 1.e30
        wmax = -1.e30
        
        # 1D Model
        sp = tfit['line1d'].wave, tfit['line1d'].flux
        spf = tfit['line1d'].wave, tfit['line1d'].flux*0+1
        
        for i in range(self.N):
            beam = self.beams[i]
            m_i = beam.compute_model(spectrum_1d=sp, is_cgs=True, in_place=False).reshape(beam.sh)
            f_i = beam.compute_model(spectrum_1d=spf, is_cgs=True, in_place=False).reshape(beam.sh)
            
            #if isinstance(beam, grizli.model.BeamCutout):
            if hasattr(beam, 'init_epsf'): # grizli.model.BeamCutout
                if beam.grism.instrument == 'NIRISS':
                    grism = beam.grism.pupil
                else:
                    grism = beam.grism.filter
                
                clean = beam.grism['SCI'] - beam.contam - tfit['cfit']['bg {0:03d}'.format(i)][0]
                
                w, fl, er = beam.beam.optimal_extract(clean, bin=bin, ivar=beam.ivar)            
                w, flm, erm = beam.beam.optimal_extract(m_i, bin=bin, ivar=beam.ivar)
                w, sens, ers = beam.beam.optimal_extract(f_i, bin=bin, ivar=beam.ivar)
                #sens = beam.beam.sensitivity                
            else:
                grism = beam.grism
                clean = beam.sci - beam.contam - tfit['cfit']['bg {0:03d}'.format(i)][0]
                w, fl, er = beam.optimal_extract(clean, bin=bin, ivar=beam.ivar)            
                w, flm, erm = beam.optimal_extract(m_i, bin=bin, ivar=beam.ivar)
                w, sens, ers = beam.optimal_extract(f_i, bin=bin, ivar=beam.ivar)
                
                #sens = beam.sens
            
            sens[~np.isfinite(sens)] = 1
            
            w = w/1.e4
                 
            unit_corr = 1./sens
            clip = (sens > 0.1*sens.max()) 
            clip &= (np.isfinite(flm)) & (er > 0)
            if clip.sum() == 0:
                continue
            
            fl *= unit_corr/1.e-19
            er *= unit_corr/1.e-19
            flm *= unit_corr/1.e-19
            
            f_alpha = 1./(self.Ngrism[grism.upper()])*0.8 #**0.5
            
            # Plot
            pscale = 1.
            if hasattr(self, 'pscale'):
                if (self.pscale is not None):
                    pscale = self.compute_scale_array(self.pscale, w[clip]*1.e4)
                    
            if show_beams:
                axc.errorbar(w[clip], fl[clip]/pscale, er[clip]/pscale, color=GRISM_COLORS[grism], alpha=f_alpha, marker='.', linestyle='None', zorder=1)
            axc.plot(w[clip], flm[clip], color='r', alpha=f_alpha, linewidth=2, zorder=10) 
              
            # Plot limits         
            ymax = np.maximum(ymax,
                        np.percentile((flm+np.median(er[clip]))[clip], 98))
            
            ymin = np.minimum(ymin, np.percentile((flm-er*0.)[clip], 2))
            
            wmax = np.maximum(wmax, w[clip].max())
            wmin = np.minimum(wmin, w[clip].min())
        
        # Cleanup
        axc.set_xlim(wmin, wmax)
        axc.semilogx(subsx=[wmax])
        #axc.set_xticklabels([])
        axc.set_xlabel(r'$\lambda$')
        axc.set_ylabel(r'$f_\lambda \times 10^{-19}$')
        #axc.xaxis.set_major_locator(MultipleLocator(0.1))
        
        axc.set_ylim(ymin-0.2*ymax, 1.2*ymax)
        axc.grid()
                
        for ax in [axc]: #[axa, axb, axc]:
            
            labels = np.arange(np.ceil(wmin/minor), np.ceil(wmax/minor))*minor
            ax.set_xticks(labels)
            ax.set_xticklabels(labels)
            #ax.set_xticklabels([])
            #print(labels, wmin, wmax)

        gs.tight_layout(fig, pad=0.1, w_pad=0.1)
        return fig
        
    def process_zfit(self, zgrid, chi2, prior=None):
        """Parse redshift fit"""
        
        zbest = zgrid[np.argmin(chi2)]
        
        ###############
        
        if prior is not None:
            #print('\n\nPrior!\n\n', chi2.min(), prior[1].min())
            interp_prior = np.interp(zgrid, prior[0], prior[1])
            chi2 += interp_prior
        else:
            interp_prior = None
            
        print(' Zoom iteration: z_best={0:.4f}\n'.format(zgrid[np.argmin(chi2)]))
        
        ### Best redshift
        if not stars:
            templates = utils.load_templates(line_complexes=False, fwhm=fwhm, fsps_templates=fsps_templates)
        
        zbest = zgrid[np.argmin(chi2)]
        ix = np.argmin(chi2)
        chibest = chi2.min()
        
        ## Fit parabola
        if (ix > 0) & (ix < len(chi2)-1):
            c = polyfit(zgrid[ix-1:ix+2], chi2[ix-1:ix+2], 2)
            zbest = -c[1]/(2*c[0])
            chibest = polyval(c, zbest)
        
        out = self.fit_at_z(z=zbest, templates=templates,
                            fitter=fitter, poly_order=poly_order, 
                            fit_background=fit_background)
        
        A, coeffs_full, chi2_best, model_full = out
        
        # Parse results
        out2 = self.parse_fit_outputs(zbest, templates, coeffs_full, A)
        line_flux, covar, cont1d, line1d, model1d, model_continuum = out2
        
        # Output dictionary with fit parameters
        fit_data = OrderedDict()
        fit_data['poly_order'] = poly_order
        fit_data['fwhm'] = fwhm
        fit_data['zbest'] = zbest
        fit_data['chibest'] = chibest
        fit_data['chi_poly'] = chi2_poly
        fit_data['zgrid'] = zgrid
        fit_data['prior'] = interp_prior
        fit_data['A'] = A
        fit_data['coeffs'] = coeffs
        fit_data['chi2'] = chi2
        fit_data['DoF'] = self.DoF
        fit_data['model_full'] = model_full
        fit_data['coeffs_full'] = coeffs_full
        fit_data['covar'] = covar
        fit_data['line_flux'] = line_flux
        #fit_data['templates_full'] = templates
        fit_data['model_cont'] = model_continuum
        fit_data['model1d'] = model1d
        fit_data['cont1d'] = cont1d
        fit_data['line1d'] = line1d
    
    def scale_to_photometry(self, z=0, templates={}, tol=1.e-4, order=0, init=None, method='Powell'):
        """Compute scale factor between spectra and photometry
        
        method : 'Powell' or 'BFGS' work well, latter a bit faster but less robust
        
        TBD
        """
        import scipy.optimize
        
        if self.Nphot == 0:
            return np.array([10.])
        
        AxT, data = self.xfit_at_z(z=z, templates=templates, fitter='nnls', fit_background=True, get_uncertainties=False, get_design_matrix=True)
        
        if init is None:
            init = np.zeros(order+1)
            init[0] = 10.
            
        scale_fit = scipy.optimize.minimize(self.objfun_scale, init, args=(AxT, data, self, 0), method=method, jac=None, hess=None, hessp=None, tol=tol, callback=None, options=None)
        
        # pscale = scale_fit.x
        return scale_fit
    
    @staticmethod
    def compute_scale_array(pscale, wave):
        """Return the scale array given the input coefficients
        TBD
        """
        N = len(pscale)
        rescale = 10**(np.arange(N)+1)
        return np.polyval((pscale/rescale)[::-1], (wave-1.e4)/1000.)
        
    @staticmethod
    def objfun_scale(pscale, AxT, data, self, return_coeffs):
        """
        Objective function for fitting for a scale term between photometry and 
        spectra
        """
        import scipy.optimize
        from scipy import polyval

        scale = self.compute_scale_array(pscale, self.wavef[self.fit_mask])
        scale[-self.Nphot:] = 1.
        Ax = (AxT.T*scale)

        # Remove scaling from background component
        for i in range(self.N):
            Ax[i,:] /= scale

        coeffs, rnorm = scipy.optimize.nnls(Ax.T, data)  
            
        full = np.dot(coeffs, Ax)
        resid = data - full# - background
        chi2 = np.sum(resid**2*self.weightf[self.fit_mask])

        print('{0} {1}'.format(pscale, chi2))

        if return_coeffs:
            return coeffs, full, resid, chi2, AxT
        else:
            return chi2
            
    def xfit_star(self, tstar=None):
        """Fit stellar templates
        """
        import matplotlib.pyplot as plt
        import matplotlib.gridspec
        from matplotlib.ticker import MultipleLocator
        
        #self = grizli.multifit.MultiBeam('ers-grism_{0:05d}.beams.fits'.format(id), fcontam=0.2, psf=True)
        #self.extend(grizli.multifit.MultiBeam('/Volumes/Pegasus/Grizli/ACS/goodss/Prep/ers-grism-pears_{0:05d}.beams.fits'.format(id), fcontam=0.2))

        if tstar is None:
            tstar = utils.load_templates(fwhm=1200, line_complexes=True, fsps_templates=True, stars=True)

        NTEMP = len(tstar)
        covar = np.zeros((NTEMP, self.N+1, self.N+1))
        coeffs = np.zeros((NTEMP, self.N+1))
        chi2 = np.zeros(NTEMP)

        types = np.array(list(tstar.keys()))

        for ik, k in enumerate(tstar):
            ts = {k:tstar[k]}
            print(k)
            chi2[ik], coeffs[ik,:], coeffs_err, covar[ik,:,:] = self.xfit_at_z(z=0, templates=ts, fitter='nnls', fit_background=True, get_uncertainties=True)

        # Initialize plot window
        Ng = len(self.grisms)
        gs = matplotlib.gridspec.GridSpec(1,2, 
                        width_ratios=[1,1.5+0.5*(Ng>1)],
                        hspace=0.)

        fig = plt.figure(figsize=[8+4*(Ng>1), 3.5])

        # p(z)
        axz = fig.add_subplot(gs[-1,0]) #121)

        axz.text(0.95, 0.96, self.group_name + '\n'+'ID={0:<5d} {1:s}'.format(self.id, types[np.argmin(chi2)].strip('stars/').strip('.txt')), ha='right', va='top', transform=axz.transAxes, fontsize=9)

        axz.plot(chi2-chi2.min(), marker='.', color='k')
        #axz.fill_between(z, (chi2-chi2.min())/scale_nu, 27, color='k', alpha=0.5)

        axz.set_xlabel(r'Sp. Type')
        axz.set_ylabel(r'$\chi^2_\nu$'+' ; '+ r'$\chi^2_\mathrm{{min}}=\frac{{{0:.0f}}}{{{1:d}}}={2:.2f}$'.format(chi2.min(), self.DoF, chi2.min()/self.DoF))
        #axz.set_yticks([1,4,9,16,25])

        if len(tstar) < 30:
            tx = [t.strip('stars/').strip('.txt') for t in types]
            axz.set_xticks(np.arange(len(tx)))
            tl = axz.set_xticklabels(tx)
            for ti in tl:
                ti.set_size(8)

        axz.set_ylim(-2, 49)
        axz.set_yticks([1,4,9,16,25])
        axz.grid()
        #axz.yaxis.set_major_locator(MultipleLocator(base=1))

        #### Spectra
        axc = fig.add_subplot(gs[-1,1]) #224)
        ymin = 1.e30
        ymax = -1.e30
        wmin = 1.e30
        wmax = -1.e30

        # 1D Model
        ix = np.argmin(chi2)
        tbest = types[ix]
        sp = tstar[tbest].wave, tstar[tbest].flux*coeffs[ix,-1]

        for i in range(self.N):
            beam = self.beams[i]
            m_i = beam.compute_model(spectrum_1d=sp, is_cgs=True, in_place=False).reshape(beam.sh)

            #if isinstance(beam, grizli.model.BeamCutout):
            if hasattr(beam, 'init_epsf'): # grizli.model.BeamCutout
                grism = beam.grism.filter
                clean = beam.grism['SCI'] - beam.contam - coeffs[ix,i]

                w, fl, er = beam.beam.optimal_extract(clean, ivar=beam.ivar)            
                w, flm, erm = beam.beam.optimal_extract(m_i, ivar=beam.ivar)
                sens = beam.beam.sensitivity                
            else:
                grism = beam.grism
                clean = beam.sci - beam.contam - coeffs[ix,i]
                w, fl, er = beam.optimal_extract(clean, ivar=beam.ivar)            
                w, flm, erm = beam.optimal_extract(m_i, ivar=beam.ivar)

                sens = beam.sens

            w = w/1.e4

            unit_corr = 1./sens
            clip = (sens > 0.1*sens.max()) 
            clip &= (np.isfinite(flm)) & (er > 0)
            if clip.sum() == 0:
                continue

            fl *= unit_corr/1.e-19
            er *= unit_corr/1.e-19
            flm *= unit_corr/1.e-19

            f_alpha = 1./(self.Ngrism[grism.upper()])*0.8 #**0.5

            # Plot
            pscale = 1.
            if hasattr(self, 'pscale'):
                if (self.pscale is not None):
                    pscale = self.compute_scale_array(self.pscale, w[clip])
                    
            axc.errorbar(w[clip], fl[clip]/pscale, er[clip]/pscale, color=GRISM_COLORS[grism], alpha=f_alpha, marker='.', linestyle='None', zorder=1)
            axc.plot(w[clip], flm[clip], color='r', alpha=f_alpha, linewidth=2, zorder=10) 

            # Plot limits         
            ymax = np.maximum(ymax,
                        np.percentile((flm+np.median(er[clip]))[clip], 98))

            ymin = np.minimum(ymin, np.percentile((flm-er*0.)[clip], 2))

            wmax = np.maximum(wmax, w[clip].max())
            wmin = np.minimum(wmin, w[clip].min())
        
        oned_spec = self.get_binned_spectra(coeffs=coeffs[ix,:])
        for g in oned_spec:

            pscale = 1.
            if hasattr(self, 'pscale'):
                if (self.pscale is not None):
                    pscale = self.compute_scale_array(self.pscale, oned_spec[g]['wave'])

            axc.errorbar(oned_spec[g]['wave']/1.e4, oned_spec[g]['flux']/1.e-19/pscale, oned_spec[g]['err']/1.e-19/pscale, color=GRISM_COLORS[g], alpha=0.8, marker='.', linestyle='None', zorder=1)
        
        # Cleanup
        axc.set_xlim(wmin, wmax)
        #axc.semilogx(subsx=[wmax])
        #axc.set_xticklabels([])
        axc.set_xlabel(r'$\lambda$')
        axc.set_ylabel(r'$f_\lambda \times 10^{-19}$')
        #axc.xaxis.set_major_locator(MultipleLocator(0.1))

        axc.set_ylim(ymin-0.2*ymax, 1.2*ymax)
        axc.grid()

        for ax in [axc]: #[axa, axb, axc]:

            labels = np.arange(np.ceil(wmin*10), np.ceil(wmax*10))/10.
            ax.set_xticks(labels)
            ax.set_xticklabels(labels)
            #ax.set_xticklabels([])
            #print(labels, wmin, wmax)

        gs.tight_layout(fig, pad=0.1, w_pad=0.1)
        
        return fig
        
        # Output TBD
        if False:
            sfit = OrderedDict()

            k = list(tstar.keys())[ix]
            ts = {k:tstar[k]}
            cont1d, line1d = utils.dot_templates(coeffs[ix,self.N:], ts, z=0.)
        
            sfit['cfit'] = {}
            sfit['coeffs'] = coeffs[ix,:]
            sfit['covar'] = covar[ix,:,:]
            sfit['z'] = 0.
            sfit['templates'] = ts
            sfit['cont1d'] = cont1d
            sfit['line1d'] = line1d
        
            return fig, sfit
    
    ### 
    ### Generic functions for generating flat model and background arrays
    ###
    def optimal_extract(self, data, bin=1, ivar=None):
        """
        TBD: split by grism
        """
        import astropy.units as u
        
        if data.size != self.fit_mask.sum():
            print('`data` has to be sized of masked arrays (self.fit_mask)')
            return False
            
        if not hasattr(self, 'optimal_profile_mask'):
            self.initialize_masked_arrays()
        
        prof = self.optimal_profile_mask
        if ivar is None:
            ivar = 1./self.sigma2_mask
            
        num = prof*data*ivar
        den = prof**2*ivar
        
        out = {}
        for grism in self.Ngrism:
            lim = utils.GRISM_LIMITS[grism]
            wave_bin = np.arange(lim[0]*1.e4, lim[1]*1.e4, lim[2]*bin)
            flux_bin = wave_bin*0.
            var_bin = wave_bin*0.
        
            for j in range(len(wave_bin)):
                ix = np.abs(self.wave_mask-wave_bin[j]) < lim[2]*bin/2.
                ix &= self.grism_name_mask == grism
                if ix.sum() > 0:
                    var_bin[j] = 1./den[ix].sum()
                    flux_bin[j] = num[ix].sum()*var_bin[j]
        
            binned_spectrum = utils.GTable()
            binned_spectrum['wave'] = wave_bin*u.Angstrom
            binned_spectrum['flux'] = flux_bin*(u.electron/u.second)
            binned_spectrum['err'] = np.sqrt(var_bin)*(u.electron/u.second)
            
            binned_spectrum.meta['BIN'] = (bin, 'Spectrum binning')
            
            out[grism] = binned_spectrum
            
        return out

            
    def initialize_masked_arrays(self):
        """
        Initialize flat masked arrays for fast likelihood calculation
        """
        try:
            # MultiBeam
            self.contamf_mask = self.contamf[self.fit_mask]
            
            p = []
            for beam in self.beams:
                beam.beam.init_optimal_profile()
                p.append(beam.beam.optimal_profile.flatten()[beam.fit_mask])
            
            self.optimal_profile_mask = np.hstack(p)
            
            # Inverse sensitivity
            self.sens_mask = np.hstack([np.dot(np.ones(beam.sh[0])[:,None], beam.beam.sensitivity[None,:]).flatten()[beam.fit_mask] for beam in self.beams])
            
            self.grism_name_mask = np.hstack([[beam.grism.filter]*beam.fit_mask.sum() for beam in self.beams])
        except:
            # StackFitter
            self.contamf_mask = np.hstack([beam.contamf[beam.fit_mask] 
                                           for beam in self.beams])

            p = []
            for beam in self.beams:
                beam.init_optimal_profile()
                p.append(beam.optimal_profile.flatten()[beam.fit_mask])
            
            self.optimal_profile_mask = np.hstack(p)
            
            # Inverse sensitivity
            self.sens_mask = np.hstack([np.dot(np.ones(beam.sh[0])[:,None], beam.sens[None,:]).flatten()[beam.fit_mask] for beam in self.beams])
            
            self.grism_name_mask = np.hstack([[beam.grism]*beam.fit_mask.sum() for beam in self.beams])
            
        self.wave_mask = np.hstack([np.dot(np.ones(beam.sh[0])[:,None], beam.wave[None,:]).flatten()[beam.fit_mask] for beam in self.beams])
            
        # (scif attribute is already contam subtracted)
        self.scif_mask = self.scif[self.fit_mask] 
        # sigma
        self.sigma_mask = 1/self.sivarf[self.fit_mask]
        # sigma-squared 
        self.sigma2_mask = 1/self.ivarf[self.fit_mask] 
                
    def get_flat_model(self, spectrum_1d, apply_mask=True):
        """
        Generate model array based on the model 1D spectrum in `spectrum_1d`

        Parameters
        ----------

        spectrum_1d : list
        
            List of 1D arrays [wavelength, flux].

        Returns
        -------

        model : Array with dimensions `(self.fit_mask.sum(),)`
        
            Flattened, masked model array.

        """
        mfull = []
        for ib, beam in enumerate(self.beams):
            model_i = beam.compute_model(spectrum_1d=spectrum_1d, is_cgs=True,
                                        in_place=False)

            if apply_mask:
                mfull.append(model_i.flatten()[beam.fit_mask])
            else:
                mfull.append(model_i.flatten())
                
        return np.hstack(mfull)

    def get_flat_background(self, bg_params, apply_mask=True):
        """
        Generate background array the same size as the flattened total 
        science array.

        Parameters
        ----------
        bg_params : array with shape (self.N) or (self.N, M)
        
            Background parameters for each beam, where the `M` axis is
            polynomial cofficients in the order expected by
            `~astropy.modeling.models.Polynomial2D`.  If the array is 1D,
            then provide a simple pedestal background.

        Returns
        -------

        bg_model : Array with dimensions `(self.fit_mask.sum(),)`
        
            Flattened, masked background array.

        """
        from astropy.modeling.models import Polynomial2D
        
        # Initialize beam pixel coordinates
        for beam in self.beams:
            needs_init = not hasattr(beam, 'xp')
            if hasattr(beam, 'xp_mask'):
                needs_init |= apply_mask is not beam.xp_mask
                
            if needs_init:
                #print('Initialize xp/yp')
                yp, xp = np.indices(beam.sh)
                xp = (xp - beam.sh[1]/2.)/(beam.sh[1]/2.)
                yp = (yp - beam.sh[0]/2.)/(beam.sh[0]/2.)
                
                if apply_mask:
                    beam.xp = xp.flatten()[beam.fit_mask]
                    beam.yp = yp.flatten()[beam.fit_mask]
                else:
                    beam.xp = xp.flatten()
                    beam.yp = yp.flatten()
                    
                beam.xp_mask = apply_mask
                
            if (not hasattr(beam, 'ones')) | needs_init:
                if apply_mask:
                    beam.ones = np.ones(beam.fit_mask.sum())
                else:
                    beam.ones = np.ones(beam.fit_mask.size)
                    
        # Initialize 2D polynomial
        poly = None
        if bg_params.ndim > 1:
            if bg_params.shape[1] > 1:
                M = bg_params.shape[1]
                order = {3:1,6:2,10:3}
                poly = Polynomial2D(order[M])

        #mfull = self.scif[self.fit_mask]
        bg_full = []

        for ib, beam in enumerate(self.beams):        
            if poly is not None:
                poly.parameters = bg_params[ib, :]
                bg_i = poly(beam.xp, beam.yp)
            else:
                # Order = 0, pedestal offset
                bg_i = beam.ones*bg_params[ib]

            bg_full.append(bg_i)

        return np.hstack(bg_full)
        
def show_drizzled_lines(line_hdu, full_line_list=['OII', 'Hb', 'OIII', 'Ha', 'SII', 'SIII'], size_arcsec=2, cmap='cubehelix_r', scale=1., dscale=1):
    """TBD
    """
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator
    
    show_lines = []
    for line in full_line_list:
        if line in line_hdu[0].header['HASLINES'].split():
            show_lines.append(line)
    
    #print(line_hdu[0].header['HASLINES'], show_lines)
    
    NL = len(show_lines)
    
    fig = plt.figure(figsize=[3*(NL+1),3.4])
    
    # Direct
    ax = fig.add_subplot(1,NL+1,1)
    ax.imshow(line_hdu['DSCI'].data*dscale, vmin=-0.02, vmax=0.6, cmap=cmap, origin='lower')
    ax.set_title('Direct   {0}    z={1:.3f}'.format(line_hdu[0].header['ID'], line_hdu[0].header['REDSHIFT']))
    
    ax.set_xlabel('RA'); ax.set_ylabel('Decl.')

    # 1" ticks
    pix_size = np.abs(line_hdu['DSCI'].header['CD1_1']*3600)
    majorLocator = MultipleLocator(1./pix_size)
    N = line_hdu['DSCI'].data.shape[0]/2
    ax.errorbar([N-0.5/pix_size], N-0.9*size_arcsec/pix_size, yerr=0, xerr=0.5/pix_size, color='k')
    ax.text(N-0.5/pix_size, N-0.9*size_arcsec/pix_size, r'$1^{\prime\prime}$', ha='center', va='bottom', color='k')

    # Line maps
    for i, line in enumerate(show_lines):
        ax = fig.add_subplot(1,NL+1,2+i)
        ax.imshow(line_hdu['LINE',line].data*scale, vmin=-0.02, vmax=0.6, cmap=cmap, origin='lower')
        ax.set_title(r'%s %.3f $\mu$m' %(line, line_hdu['LINE', line].header['WAVELEN']/1.e4))

    # End things
    for ax in fig.axes:
        ax.set_yticklabels([]); ax.set_xticklabels([])
        ax.set_xlim(N+np.array([-1,1])*size_arcsec/pix_size)
        ax.set_ylim(N+np.array([-1,1])*size_arcsec/pix_size)

        ax.xaxis.set_major_locator(majorLocator)
        ax.yaxis.set_major_locator(majorLocator)

    fig.tight_layout(pad=0.1, w_pad=0.5)
    return fig

