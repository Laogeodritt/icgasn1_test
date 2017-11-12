import logging
import sys

from time import sleep, perf_counter

import numpy as np

from pymeasure.log import console_log
from pymeasure.adapters import SerialAdapter
from pymeasure.display.Qt import QtGui
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import Procedure, Results
from pymeasure.experiment import IntegerParameter, FloatParameter, \
                                 BooleanParameter, Parameter

from pymeasure.instruments.srs import SR830

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

class AcFreqProcedure(Procedure):
    _resource = None
    _is_serial = None

    frequency = FloatParameter(
        'Frequency', units='Hz', minimum=0.001, maximum=102000.0, default=100.0)
    harmonic = IntegerParameter(
        'Harmonic', minimum=1, maximum=1000000, default=1)
    phase = FloatParameter(
        'Phase', units='°', minimum=-180.0, maximum=+180.0, default=0)
    tolerance = FloatParameter(
        "Tolerance", minimum=0, maximum=1, default=0.01)
    window = IntegerParameter(
        "TestWindow", units="x100 samples", minimum=1, maximum=20)

    tau = FloatParameter("Time Constant", units="s")
    slope = IntegerParameter("Slope", units="dB/8va")
    tsamp = FloatParameter("Sample time", units="s")

    meas_r = FloatParameter("R", units="VRMS")
    meas_phi = FloatParameter("THETA", units="°")
    deviation = FloatParameter("ΔR", units="%")

    TIME_CONSTANTS_MAP = {
        102000: 0.001,
        10000: 0.01,
        1000: 0.1,
        100: 1.0,
        10: 3.0,
        1: 10.0
    }
    SLOPES_MAP = {102000: 24}
    MIN_SAMPLE_TIME = 0.1
    

    DATA_COLUMNS = ['Time (s)', 'Magnitude (VRMS)', 'Phase (deg)']

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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self._resource is None or self._is_serial is None:
            raise RuntimeError("Must call {}.configure() first".
                format(self.__class__))

    def startup(self):
        log.info("Connecting to and configuring SR830...")
        self.lia = SR830(
            resource,
            SR830.OutputInterface.RS232 if is_serial else SR830.OutputInterface.GPIB)
        if ",SR830," not in self.lia.id:
            errmsg = "Device '{}' is not an SR830!".format(str(resource))
            log.error(errmsg)
            raise RuntimeError(errmsg)
        log.debug("Resetting...")
        self.lia.reset() # see the Standard Settings p4-4 of the manual
        self.lia.sine_voltage = 0.010 # default 1Vrms should be OK but let's do this quickly
        sleep(0.2) # reset takes time
        log.debug("Configuring...")
        log.info("Parameters: f={:.4e} Hz n={:d} phi={:+.2f} deg".format(
            self.frequency, self.harmonic, self.phase))
        self.lia.ref_source = 'internal'
        self.lia.frequency = self.frequency
        self.lia.harmonic = self.harmonic
        self.lia.phase = self.phase
        self.lia.input_mode = 'a-b' if self.frequency < 200 else 'a'
        self.lia.input_ground = 'float'
        self.lia.input_coupling = 'dc' if self.frequency < 200 else 'ac'
        self.lia.input_line_filter = 'off'
        self.lia.channel1 = 'R'
        self.lia.channel2 = 'Theta'
        self.lia.channel1_out = 'display'
        self.lia.channel2_out = 'display'
        self.lia.sensitivity = 20e-3
        self.lia.time_constant, self.lia.filter_slope = self.get_filter(self.frequency)
        self.lia.reserve = "low"
        self.lia.sync_filter = True

        self.sample_time = max(self.lia.time_constant / 100, MIN_SAMPLE_TIME)
        self.sample_window = self.window * 100

        log.info("Auto-parameters: tau={:.1e} s slope={:.0f} dB/octave".format(
            self.lia.time_constant, self.lia.slope))
        log.info("Test parameters: Tsamp={:.1f} s tol={:.2%} window={:d}".format(
            self.sample_time, self.tolerance, self.window*100))

        self.lia.enable_lia_status(input_=True, filter_=True, output=True)

    def get_filter(self, frequency):
        """
        Given a frequency, return the recommended (time_constant, slope) tuple.
        """
        tau_key = min(filter(lambda f: f >= frequency, self.TIME_CONSTANTS_MAP))
        slope_key = min(filter(lambda f: f >= frequency, self.SLOPES_MAP))
        return self.TIME_CONSTANTS_MAP[tau_key], self.SLOPES_MAP[slope_key]

    def execute(self):
        time = []
        r = []
        phi = []
        next_time = 0

        start_clk = perf_counter()
        next_clk = start_clk

        # record until stabilised
        while True:
            sleep(max(next_clk - perf_counter(), 0))
            next_clk += self.sample_time

            time.append(next_time)
            next_time += self.sample_time
            r.append(self.lia.magnitude)
            phi.append(self.lia.theta)

            self.emit('results', {
                'Time (s)': time[-1],
                'Magnitude (VRMS)': r[-1],
                'Phase (deg)': phi[-1]
            })

            # only check stop cond after gathering enough data
            if len(r) >= self.sample_window:
                r_window = r[-self.sample_window]
                r_avg = sum(r_window) / self.sample_window
                deviation = max(abs(r_avg - max(r_window)), abs(r_avg - min(r_window)))

                phi_window = phi[-self.sample_window]
                phi_avg = sum(phi_window) / self.sample_window

                self.meas_r = r_avg
                self.meas_phi = phi_avg
                self.deviation = deviation / r_avg * 100

                # measurement has settled - stop measuring
                if self.deviation <= self.tolerance*100:
                    log.info("Deviation {:.2f}% < {:.2%}. Stopping.".format(
                        self.deviation, self.tolerance))
                    log.info("Final measurement: {:.6f} VRMS {:.6f} DEG".format(
                        self.meas_r, self.meas_phi))
                    break
            else:
                self.meas_r = 0
                self.meas_phi = 0
                self.deviation = 100

            if self.should_stop():
                log.info("User aborted the procedure")
                break

    def shutdown(self):
        self.lia.shutdown()
        log.info("Finished measuring")


class AcSweepProcedure(AcFreqProcedure):
    _resource = None
    _is_serial = None

    fmin = FloatParameter('Start frequency', units='Hz',
        minimum=0.001, maximum=102000.0, default=0.050)
    fmax = FloatParameter('Stop frequency', units='Hz',
        minimum=0.001, maximum=102000.0, default=10000)
    points_per_decade = IntegerParameter('Points per decade',
        minimum=1, maximum=100, default=6)
    harmonic = IntegerParameter(
        'Harmonic', minimum=1, maximum=1000000, default=1)
    phase = FloatParameter(
        'Phase', units='°', minimum=-180.0, maximum=+180.0, default=0)
    tolerance = FloatParameter(
        "Tolerance", minimum=0, maximum=1, default=0.01)
    window = IntegerParameter(
        "TestWindow", units="x100 samples", minimum=1, maximum=20)

    TIME_CONSTANTS_MAP = {
        102000: 0.001,
        10000: 0.01,
        1000: 0.1,
        100: 1.0,
        10: 3.0,
        1: 10.0
    }
    SLOPES_MAP = {102000: 24}
    MIN_SAMPLE_TIME = 0.1
    INPUT_VOLTAGE = 0.01
    

    DATA_COLUMNS = ['Frequency (Hz)', 'Magnitude (VRMS)', 'Magnitude (dB)', 'Phase (deg)']

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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self._resource is None or self._is_serial is None:
            raise RuntimeError("Must call {}.configure() first".
                format(self.__class__))

    def startup(self):
        log.info("Connecting to and configuring SR830...")
        self.lia = SR830(
            resource,
            SR830.OutputInterface.RS232 if is_serial else SR830.OutputInterface.GPIB)
        if ",SR830," not in self.lia.id:
            errmsg = "Device '{}' is not an SR830!".format(str(resource))
            log.error(errmsg)
            raise RuntimeError(errmsg)
        log.debug("Resetting...")
        self.lia.reset() # see the Standard Settings p4-4 of the manual
        self.lia.sine_voltage = INPUT_VOLTAGE # default 1Vrms should be OK but let's do this quickly
        sleep(0.2) # reset takes time
        log.debug("Configuring...")
        log.info("Parameters: fmin={:.4e} Hz fmax={:.4e} Hz pts={:d}/dec n={:d} phi={:+.2f} deg ac={:s}".format(
            self.fmin, self.fmax, self.points_per_decade, self.harmonic, self.phase, self.is_ac_coupled))
        self.lia.ref_source = 'internal'
        self.lia.frequency = self.frequency
        self.lia.harmonic = self.harmonic
        self.lia.phase = self.phase
        self.lia.input_ground = 'float'
        self.lia.input_line_filter = 'off'
        self.lia.channel1 = 'R'
        self.lia.channel2 = 'Theta'
        self.lia.channel1_out = 'display'
        self.lia.channel2_out = 'display'
        self.lia.sensitivity = 20e-3
        self.lia.reserve = "low"
        self.lia.sync_filter = True

        self.sample_window = self.window * 100

        self.lia.enable_lia_status(input_=True, filter_=True, output=True)

    def get_filter(self, frequency):
        """
        Given a frequency, return the recommended (time_constant, slope) tuple.
        """
        tau_key = min(filter(lambda f: f >= frequency, self.TIME_CONSTANTS_MAP))
        slope_key = min(filter(lambda f: f >= frequency, self.SLOPES_MAP))
        return self.TIME_CONSTANTS_MAP[tau_key], self.SLOPES_MAP[slope_key]

    def execute(self):
        num_points = int(np.log10(self.fmax/self.fmin) + self.points_per_decade)
        freqs = np.geomspace(self.fmin, self.fmax, num_points)

        for freq in freqs:
            log.info("Measuring @ frequency: {:.3f}".format(freq))
            r, phi = execute_single_freq(freq)

            self.emit('results', {
                'Frequency (Hz)': freq,
                'Magnitude (VRMS)': r,
                'Magnitude (dB)': 20*np.log10(r/self.INPUT_VOLTAGE),
                'Phase (deg)': phi
            })


    def execute_single_freq(self, frequency):
        self.configure_next_freq(frequency)

        time = []
        r = []
        phi = []
        next_time = 0

        log.debug("Starting measurement...")

        start_clk = perf_counter()
        next_clk = start_clk

        # record until stabilised
        while True:
            sleep(max(next_clk - perf_counter(), 0))
            next_clk += self.sample_time

            time.append(next_time)
            next_time += self.sample_time
            r.append(self.lia.magnitude)
            phi.append(self.lia.theta)

            # only check stop cond after gathering enough data
            if len(r) >= self.sample_window:
                r_window = r[-self.sample_window]
                r_avg = sum(r_window) / self.sample_window
                deviation = max(abs(r_avg - max(r_window)), abs(r_avg - min(r_window)))

                phi_window = phi[-self.sample_window]
                phi_avg = sum(phi_window) / self.sample_window

                # measurement has settled - stop measuring
                if self.deviation <= self.tolerance*100:
                    # TODO: timeout?
                    log.info("Deviation {:.2%} < {:.2%}. Stopping.".format(
                        deviation / r_avg, self.tolerance))
                    log.info("Final measurement: {:.6f} VRMS {:.6f} DEG".format(
                        r_avg, phi_avg))
                    return r_avg, phi_avg
            else:
                self.meas_r = 0
                self.meas_phi = 0
                self.deviation = 100

            if self.should_stop():
                log.info("User aborted the procedure")
                break

        return None

    def configure_next_freq(self, frequency):
        self.lia.input_mode = 'a-b' if frequency < 200 else 'a'
        self.lia.input_coupling = 'dc' if frequency < 200 else 'ac'
        self.lia.time_constant, self.lia.filter_slope = self.get_filter(frequency)
        self.sample_time = max(self.lia.time_constant / 100, MIN_SAMPLE_TIME)
        log.debug("Configuring frequency: {:.3f}".format(frequency))
        log.debug("Auto-parameters: tau={:.1e} s slope={:.0f} dB/octave".format(
            self.lia.time_constant, self.lia.slope))
        log.debug("Test parameters: Tsamp={:.1f} s tol={:.2%} window={:d}".format(
            self.sample_time, self.tolerance, self.window*100))

    def shutdown(self):
        self.lia.shutdown()
        log.info("Finished measuring")


class SweepWindow(ManagedWindow):
    file_series = 0

    def __init__(self):
        super().__init__(
            procedure_class=AcSweepProcedure,
            inputs=['fmin', 'fmax', 'points_per_decade', 'harmonic', 'phase', 'tolerance', 'window'],
            displays=['frequency', 'harmonic', 'phase', 'tolerance', 'window'],
            x_axis='Frequency (Hz)',
            y_axis='Magnitude (dB)'
        )
        self.setWindowTitle('AC Sweep Measurement')

    def queue(self):
        filename = "sr830_acsweep_{:d}.csv".format(self.file_series)
        self.file_series += 1

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)


class MainWindow(ManagedWindow):
    file_series = 0

    def __init__(self):
        super(MainWindow, self).__init__(
            procedure_class=AcFreqProcedure,
            inputs=['frequency', 'harmonic', 'phase', 'tolerance', 'window'],
            displays=['frequency', 'harmonic', 'phase', 'tolerance', 'window',
            'tau', 'slope', 'tsamp', "meas_r", "meas_phi", "deviation"],
            x_axis='Time (s)',
            y_axis='Magnitude (VRMS)'
        )
        self.setWindowTitle('AC Single-Frequency Measurement')

    def queue(self):
        filename = "sr830_acfreq_{:d}.csv".format(self.file_series)
        self.file_series += 1

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)


if __name__ == "__main__":
    adapter = SerialAdapter('dev/ttyUSB0', baudrate=9600, rtscts=True, timeout=5)
    AcFreqProcedure.configure(adapter, True)

    # TODO: window selection
    # TODO: DRY

    app = QtGui.QApplication(sys.argv)
    app.setStyle("plastique")
    window = SweepWindow()
    window.show()
    sys.exit(app.exec_())
