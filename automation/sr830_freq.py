import logging
import sys
from collections import deque
import time

import numpy as np
import argparse

# Let's prefer PyQt5, if available
# PyMeasure/PyQtGraph will use an already-imported module
try:
    import PyQt5
except ImportError:
    pass

from pymeasure.log import console_log
from pymeasure.adapters import SerialAdapter
from pymeasure.display.Qt import QtGui, QtCore
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results
from pymeasure.experiment import IntegerParameter, FloatParameter, \
                                 BooleanParameter, Parameter
from pymeasure.experiment import unique_filename

from pymeasure.instruments.srs import SR830, FakeSR830Adapter, FakeSR830DUT
from pymeasure.adapters import FakeScpiAdapter

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Sr830ConfigureMixin(object):
    @classmethod
    def configure(cls, resource, is_serial=False):
        """
        :param resource: resource name string or a PyMeasure adapter object
            referring to this instrument's connection
        :param is_serial: If True, using RS232 interface, otherwise using GPIB.
            This is required to correctly configure the instrument's output
            interface, as it does not auto-detect which interface commands are
            being sent from.
        """
        cls._resource = resource
        cls._is_serial = is_serial

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._resource is None or self._is_serial is None:
            raise RuntimeError("Must call {}.configure() first".
                format(self.__class__))


class AcFreqProcedure(Procedure, Sr830ConfigureMixin):
    _resource = None
    _is_serial = None

    frequency = FloatParameter(
        'Frequency', units='Hz', minimum=0.001, maximum=102000.0, default=100.0)
    harmonic = IntegerParameter(
        'Harmonic', minimum=1, maximum=1000000, default=1)
    phase = FloatParameter(
        'Phase', units='°', minimum=-180.0, maximum=+180.0, default=0)
    tolerance = FloatParameter("Tolerance", units='%', minimum=0, maximum=100, default=1)
    window = IntegerParameter(
        "TestWindow", units="samples", minimum=100, maximum=2000)
    reset = True

    auto_tau = FloatParameter("Time Constant", units="s")
    auto_slope = IntegerParameter("Slope", units="dB/8va")
    auto_tsamp = FloatParameter("Sample time", units="s")
    auto_timeout = FloatParameter("Timeout", units='s')

    TIME_CONSTANTS_MAP = {
        102000: 0.001,
        10000: 0.01,
        1000: 0.1,
        100: 1.0,
        10: 3.0,
        1: 10.0
    }
    SLOPES_MAP = {102000: 24}
    MIN_SAMPLE_TIME = 0.01
    
    COL_T = 'Time (s)'
    COL_R = 'Magnitude (VRMS)'
    COL_THETA = 'Phase (deg)'
    COL_RS = 'Magnitude[smooth] (VRMS)'
    COL_THETAS = 'Phase[smooth] (deg)'
    COL_DEV = 'Max deviation from the mean (%)'
    DATA_COLUMNS = [COL_T, COL_R, COL_THETA, COL_RS, COL_THETAS, COL_DEV]


    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meas_window = dict.fromkeys(self.DATA_COLUMNS)


    def generate_auto_parameters(self):
        """
        Parameters like tau, filter slope and sample time can be auto-calculated
        with recommended values. This method will update all auto_* parameters,
        based on the currently-set parameters.
        """
        p_freq = self.frequency

        # finds the minimum frequency in the map that is >= p_freq
        tau_key = min(filter(lambda f: f >= p_freq, self.TIME_CONSTANTS_MAP))
        self.auto_tau = self.TIME_CONSTANTS_MAP[tau_key]

        slope_key = min(filter(lambda f: f >= p_freq, self.SLOPES_MAP))
        self.auto_slope = self.SLOPES_MAP[slope_key]

        self.auto_tsamp = max(self.auto_tau / 10, self.MIN_SAMPLE_TIME)

        self.auto_timeout = 100 * self.auto_tau


    def startup(self):
        for name in self.meas_window:
            self.meas_window[name] = deque(maxlen=self.window)
        self.meas_window['OVF'] = deque(maxlen=self.window)

        log.info("Connecting to and configuring SR830...")
        output_interface = SR830.OutputInterface.RS232 if self._is_serial \
            else SR830.OutputInterface.GPIB
        if isinstance(self._resource, FakeScpiAdapter):
            log.info("CURRENTLY IN SIMULATION MODE")
        self.lia = SR830(self._resource, output_interface)

        # Verify identity
        if ",SR830," not in self.lia.id:
            errmsg = "Device '{!r}' is not an SR830!".format(self._resource)
            log.error(errmsg)
            raise RuntimeError(errmsg)

        if self.reset:
            log.debug("Resetting...")
            self.lia.reset() # see the Standard Settings p4-4 of the manual
            self.lia.sine_voltage = 0.010 # default 1Vrms should be OK but let's do this quickly
            time.sleep(1) # reset takes time
        else:
            self.lia.sine_voltage = 0.010

        log.debug("Configuring...")
        log.info("Parameters: f={:.4e} Hz n={:d} phi={:+.2f} deg".format(
            self.frequency, self.harmonic, self.phase))
        log.info("Auto-parameters: tau={:.1e} s slope={:.0f} dB/octave".format(
            self.auto_tau, self.auto_slope))
        log.info("Test parameters: Tsamp={:.1f} s tol={:.2f}% window={:d} samples".format(
            self.auto_tsamp, self.tolerance, self.window))

        if self.reset:
            self.lia.ref_source = 'internal'
            self.lia.input_ground = 'float'
            self.lia.input_line_filter = 'off'
            self.lia.channel1 = 'R'
            self.lia.channel2 = 'Theta'
            self.lia.channel1_out = 'display'
            self.lia.channel2_out = 'display'
            self.lia.sensitivity = 20e-3
            self.lia.reserve = "low"
            self.lia.sync_filter = True

        self.lia.frequency = self.frequency
        self.lia.harmonic = self.harmonic
        self.lia.phase = self.phase
        self.lia.input_mode = 'a-b' if self.frequency < 200 else 'a'
        self.lia.input_coupling = 'dc' if self.frequency < 200 else 'ac'
        self.lia.time_constant = self.auto_tau
        self.lia.filter_slope = self.auto_slope

        self.lia.enable_lia_status(input_=True, filter_=True, output=True)


    def execute(self):
        log.info("Start measuring")

        self.event_loop = QtCore.QEventLoop()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.execute_sample)
        self.timer.start(int(self.auto_tsamp*1000))
        self.event_loop.exec()
        self.timer.stop()

        log.info("Finished measuring")


    def execute_sample(self):
        # measure immediately and with minimum time between the two values
        # exact simultaneity is not necessary
        cur_r = self.lia.magnitude
        cur_theta = self.lia.theta

        cur_meas = dict.fromkeys(self.DATA_COLUMNS)
        last_time = self.get_last_time()

        if last_time is not None:
            cur_meas[self.COL_T] = last_time + self.auto_tsamp
        else:
            cur_meas[self.COL_T] = 0
        cur_meas[self.COL_R] = cur_r
        cur_meas[self.COL_THETA] = cur_theta
        cur_meas['OVF'] = self.lia.is_input_overload() or \
                          self.lia.is_filter_overload() or \
                          self.lia.is_output_overload()

        log.debug('t = %.2f, win=%d',
            cur_meas[self.COL_T], len(self.meas_window[self.COL_T]))

        # store measurements so far - meas_window partially desync'd columns
        # want to add R, THETA to more easily calculate means
        for name in (self.COL_T, self.COL_R, self.COL_THETA, 'OVF'):
            self.meas_window[name].append(cur_meas[name])

        # calculate and store R mean, deviation
        rr = self.meas_window[self.COL_R]
        cur_meas[self.COL_RS] = sum(rr) / len(rr)

        abs_dev = max(
            abs(cur_meas[self.COL_RS] - max(self.meas_window[self.COL_R])),
            abs(cur_meas[self.COL_RS] - min(self.meas_window[self.COL_R]))
        )
        cur_meas[self.COL_DEV] = 100 * abs_dev / cur_meas[self.COL_RS]

        # calculate and store theta mean
        tt = self.meas_window[self.COL_THETA]
        cur_meas[self.COL_THETAS] = sum(tt) / len(tt)

        # store measurements so far - meas_window columns all in sync
        for name in (self.COL_RS, self.COL_DEV, self.COL_THETAS):
            self.meas_window[name].append(cur_meas[name])

        self.emit('results', cur_meas)

        # if we have a full window and no overload, start looking for endpoint
        if self.is_meas_window_full() and not any(self.meas_window['OVF']):
            # stop point: measurement variation within tolerance
            if cur_meas[self.COL_DEV] <= self.tolerance:
                log.info("Deviation {:.2f}% < {:.2%}. Stopping.".format(
                    cur_meas[self.COL_DEV], self.tolerance))
                log.info("Final measurement: {:.6f} VRMS {:.6f} DEG".format(
                    cur_meas[self.COL_RS], cur_meas[self.COL_THETAS]))
                self.event_loop.quit()
            # stop point: timeout
            elif cur_meas[self.COL_T] >= self.auto_timeout:
                log.warning("Timeout {:.2f}s elapsed. Stopping.".format(
                    self.auto_timeout))
                log.info("Final measurement: {:.6f} VRMS {:.6f} DEG".format(
                    cur_meas[self.COL_RS], cur_meas[self.COL_THETAS]))
                self.event_loop.quit()

        if self.should_stop():
            log.info("User aborted the procedure")
            self.event_loop.quit()


    def is_meas_window_full(self):
        win = self.meas_window[self.COL_T]
        return (len(win) == win.maxlen)


    def get_last_time(self):
        try:
            return self.meas_window[self.COL_T][-1]
        except IndexError:
            return None


    def shutdown(self):
        log.info("Shutting down %s" % self.__class__.__name__)
        self.lia.shutdown()


class MainWindow(ManagedWindow):
    def __init__(self):
        super(MainWindow, self).__init__(
            procedure_class=AcFreqProcedure,
            inputs=['frequency', 'harmonic', 'phase', 'tolerance', 'window'],
            displays=['frequency', 'harmonic', 'phase', 'tolerance', 'window',
                'auto_tau', 'auto_slope', 'auto_tsamp'],
            x_axis=AcFreqProcedure.COL_T,
            y_axis=AcFreqProcedure.COL_R
        )
        self.setWindowTitle('AC Single-Frequency Measurement')

    def queue(self):
        filename = unique_filename('.', prefix="data_lia")

        procedure = self.make_procedure()
        procedure.generate_auto_parameters()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='AC Single-Frequency Measurement')
    parser.add_argument('--simulate', '-s', default=False, action='store_true')
    args = parser.parse_args()

    if not args.simulate:
        adapter = SerialAdapter('/dev/ttyUSB0', baudrate=9600, rtscts=True, timeout=5)
    else:
        dut = FakeSR830DUT(50e-3, 10000)
        adapter = FakeSR830Adapter(dut)

    log.setLevel(logging.DEBUG)

    AcFreqProcedure.configure(adapter, True)
    app = QtGui.QApplication(sys.argv)
    app.setStyle("plastique")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
