__author__ = 'sstober'

from mne.filter import low_pass_filter, high_pass_filter, band_pass_filter, band_stop_filter, notch_filter

from mne import pick_types
import numpy as np
import librosa

class MNEFilterWrapper():

    def __init__(self, type, params):
        self.type = type
        self.params = params


    def process(self, data):
        if self.type == 'low-pass':
            return low_pass_filter(data, **self.params)
        elif self.type == 'high-pass':
            return high_pass_filter(data, **self.params)
        elif self.type == 'band-pass':
            return band_pass_filter(data, **self.params)
        elif self.type == 'band-stop':
            return band_stop_filter(data, **self.params)
        elif self.type == 'notch':
            return notch_filter(data, **self.params)
        else:
            raise ValueError('Unsupported filter type: {}'.format(self.type))


## old interface from mne/filter.py:
# def resample(self, sfreq, npad=100, window='boxcar',
#              stim_picks=None, n_jobs=1, verbose=None):
def fast_resample_mne(raw, sfreq, stim_picks=None, preserve_events=True, res_type='sinc_best', verbose=None):
    """Resample data channels.

    Resamples all channels. The data of the Raw object is modified inplace.

    The Raw object has to be constructed using preload=True (or string).

    WARNING: The intended purpose of this function is primarily to speed
    up computations (e.g., projection calculation) when precise timing
    of events is not required, as downsampling raw data effectively
    jitters trigger timings. It is generally recommended not to epoch
    downsampled data, but instead epoch and then downsample, as epoching
    downsampled data jitters triggers.

    Parameters
    ----------
    raw : nme raw object
        Raw data to filter.
    sfreq : float
        New sample rate to use.
    stim_picks : array of int | None
        Stim channels. These channels are simply subsampled or
        supersampled (without applying any filtering). This reduces
        resampling artifacts in stim channels, but may lead to missing
        triggers. If None, stim channels are automatically chosen using
        mne.pick_types(raw.info, meg=False, stim=True, exclude=[]).
    res_type : str
        If `scikits.samplerate` is installed, :func:`librosa.core.resample`
        will use ``res_type``. (Chooae between 'sinc_fastest', 'sinc_medium'
        and 'sinc_best' for the desired speed-vs-quality trade-off.)
        Otherwise, it will fall back on `scipy.signal.resample`
    verbose : bool, str, int, or None
        If not None, override default verbose level (see mne.verbose).
        Defaults to self.verbose.

    Notes
    -----
    For some data, it may be more accurate to use npad=0 to reduce
    artifacts. This is dataset dependent -- check your data!
    """
    self = raw  # this keeps the mne code intact

    if not self.preload:
        raise RuntimeError('Can only resample preloaded data')
    sfreq = float(sfreq)
    o_sfreq = float(self.info['sfreq'])

    offsets = np.concatenate(([0], np.cumsum(self._raw_lengths)))
    new_data = list()
    # set up stim channel processing
    if stim_picks is None:
        stim_picks = pick_types(self.info, meg=False, ref_meg=False,
                                stim=True, exclude=[])
    stim_picks = np.asanyarray(stim_picks)

    ### begin new code: save events in each stim channel ###
    if preserve_events:
        stim_events = dict()
        for sp in stim_picks:
            stim_channel_name = raw.ch_names[sp]
            if verbose:
                print 'saving events for stim channel "{}" (#{})'.format(stim_channel_name, sp)
            stim_events[sp] = mne.find_events(raw, stim_channel=stim_channel_name, shortest_event=0)
    ### end new code: save events in each stim channel ###

    ratio = sfreq / o_sfreq
    for ri in range(len(self._raw_lengths)):
        data_chunk = self._data[:, offsets[ri]:offsets[ri + 1]]

        ### begin changed code ###
#         new_data.append(resample(data_chunk, sfreq, o_sfreq, npad,
#                                  n_jobs=n_jobs))
        if verbose:
            print 'resampling {} channels...'.format(len(data_chunk))
        new_data_chunk = list()
        for i, channel in enumerate(data_chunk):
            if verbose:
                print 'processing channel #{}'.format(i)
            # TODO: this could easily be parallelized
            new_data_chunk.append(librosa.resample(channel, o_sfreq, sfreq, res_type=res_type))

        new_data_chunk = np.vstack(new_data_chunk)
        if verbose:
            print 'data shape after resampling: {}'.format(new_data_chunk.shape)
        new_data.append(new_data_chunk)
        ### end changed code ###

        new_ntimes = new_data[ri].shape[1]

        # Now deal with the stim channels. In empirical testing, it was
        # faster to resample all channels (above) and then replace the
        # stim channels than it was to only resample the proper subset
        # of channels and then use np.insert() to restore the stims

        # figure out which points in old data to subsample
        # protect against out-of-bounds, which can happen (having
        # one sample more than expected) due to padding
        stim_inds = np.minimum(np.floor(np.arange(new_ntimes)
                                        / ratio).astype(int),
                               data_chunk.shape[1] - 1)
        for sp in stim_picks:
            new_data[ri][sp] = data_chunk[[sp]][:, stim_inds]

        self._first_samps[ri] = int(self._first_samps[ri] * ratio)
        self._last_samps[ri] = self._first_samps[ri] + new_ntimes - 1
        self._raw_lengths[ri] = new_ntimes

    # adjust affected variables
    self._data = np.concatenate(new_data, axis=1)
    self.first_samp = self._first_samps[0]
    self.last_samp = self.first_samp + self._data.shape[1] - 1
    self.info['sfreq'] = sfreq
    self._times = (np.arange(self.n_times, dtype=np.float64)
                   / self.info['sfreq'])

    ### begin new code: restore save events in each stim channel ###
    if preserve_events:
        for sp in stim_picks:
            raw._data[sp,:].fill(0)     # delete data in stim channel

            if verbose:
                stim_channel_name = raw.ch_names[sp]
                print 'restoring events for stim channel "{}" (#{})'.format(stim_channel_name, sp)

            # scale onset times
            for event in stim_events[sp]:
                onset = np.floor(event[0] * ratio)
                event_id = event[2]
                if raw._data[sp,onset] > 0:
                    print '! event collision at {}: old={} new={}. Using onset+1'.format(
                                onset, raw._data[sp,onset], event_id)
                    raw._data[sp,onset+1] = event_id
                else:
                    raw._data[sp,onset] = event_id
    ### end new code: save events in each stim channel ###