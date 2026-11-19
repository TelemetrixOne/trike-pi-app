"""Voltage-based battery estimate and explicit pack replacement, without GPIO I/O.

The five-cell curve is an initial lithium-ion reference, not a measured DeWalt
DCB184 discharge curve. Field calibration is required for percentage accuracy.
"""
from collections import deque
import math
import statistics


REFERENCE_CURVE = ((15.0, 0), (16.5, 5), (17.5, 10), (18.0, 20),
                   (18.5, 40), (19.0, 60), (19.5, 75), (20.0, 85),
                   (20.5, 95), (21.0, 100))


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_soc_config(config):
    if not isinstance(config, dict):
        raise ValueError('gpio.adc.battery_soc must be a mapping')
    allowed = {'enabled', 'profile', 'voltage_soc_curve', 'stable_seconds',
               'stable_spread_v', 'valid_min_voltage', 'valid_max_voltage', 'filter_seconds'}
    if set(config) - allowed:
        raise ValueError('Unknown battery_soc configuration field')
    if type(config.get('enabled', False)) is not bool:
        raise ValueError('battery_soc.enabled must be boolean')
    if config.get('profile', 'liion_5s_reference') != 'liion_5s_reference':
        raise ValueError('Unsupported battery SOC profile')
    for name, default, low, high in (
        ('stable_seconds', 5, 2, 30), ('stable_spread_v', 0.15, 0.01, 1),
        ('valid_min_voltage', 12.5, 10, 18), ('valid_max_voltage', 21.5, 20, 23),
        ('filter_seconds', 10, 1, 60),
    ):
        value = config.get(name, default)
        if not finite_number(value) or not low <= value <= high:
            raise ValueError(f'battery_soc.{name} must be between {low} and {high}')
    lower = config.get('valid_min_voltage', 12.5)
    upper = config.get('valid_max_voltage', 21.5)
    curve = config.get('voltage_soc_curve', REFERENCE_CURVE)
    if not isinstance(curve, (tuple, list)) or not 3 <= len(curve) <= 32:
        raise ValueError('Battery voltage curve requires 3 to 32 points')
    previous = None
    for point in curve:
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(map(finite_number, point)):
            raise ValueError('Battery curve points must be [voltage, percentage]')
        voltage, percentage = point
        if not lower <= voltage <= upper or not 0 <= percentage <= 100:
            raise ValueError('Battery curve point outside measurement/percentage bounds')
        if previous and (voltage <= previous[0] or percentage <= previous[1]):
            raise ValueError('Battery curve voltage and percentage must strictly increase')
        previous = point
    if curve[0][1] != 0 or curve[-1][1] != 100:
        raise ValueError('Battery curve must span 0 to 100 percent')


def voltage_soc(voltage, curve):
    if voltage <= curve[0][0]:
        return 0.0
    for (low_v, low_soc), (high_v, high_soc) in zip(curve, curve[1:]):
        if voltage <= high_v:
            return low_soc + (voltage - low_v) * (high_soc - low_soc) / (high_v - low_v)
    return 100.0


class BatteryEstimator:
    def __init__(self, config, sample_seconds=1):
        validate_soc_config(config)
        self.curve = config.get('voltage_soc_curve', REFERENCE_CURVE)
        self.quality = 'configured_curve_estimate' if 'voltage_soc_curve' in config else 'reference_estimate'
        self.stable_seconds = config.get('stable_seconds', 5)
        self.spread = config.get('stable_spread_v', 0.15)
        self.minimum = config.get('valid_min_voltage', 12.5)
        self.maximum = config.get('valid_max_voltage', 21.5)
        self.filter_seconds = config.get('filter_seconds', 10)
        self.max_gap = max(3, sample_seconds * 3)
        self.settle_timeout = max(30, self.stable_seconds + sample_seconds * 6)
        self.samples = deque(maxlen=512)
        self.recent_voltage = deque(maxlen=3)
        self.filtered_voltage = None
        self.soc = None
        self.ah_used = 0.0
        self.last_sample = None
        self.state = 'settling'
        self.reason = 'waiting_for_stable_voltage'
        self.request_id = None
        self.pending_until = None
        self.initialised = False

    def request_swap(self, request_id, now):
        if self.pending_until is not None:
            return self.request_id == request_id
        self.request_id = request_id
        self.pending_until = now + self.settle_timeout
        self.samples.clear()
        self.recent_voltage.clear()
        self.filtered_voltage = None
        self.last_sample = None
        self.state = 'settling'
        self.reason = 'waiting_for_stable_voltage'
        return True

    def invalidate(self, reason):
        self.state = 'invalid'
        self.reason = reason
        self.samples.clear()
        self.recent_voltage.clear()
        self.last_sample = None
        self.filtered_voltage = None

    def poll(self, now):
        if self.pending_until is not None and now >= self.pending_until:
            self.pending_until = None
            self.initialised = False
            self.soc = None
            self.state = 'rejected'
            self.reason = 'voltage_not_stable_before_timeout'
            return
        if self.last_sample is not None and now - self.last_sample > self.max_gap:
            self.invalidate('battery_measurement_stale')
            self.state = 'stale'

    def update(self, voltage, current, now):
        self.poll(now)
        if self.state == 'rejected':
            return
        if not finite_number(voltage) or not self.minimum <= voltage <= self.maximum:
            self.invalidate('battery_voltage_out_of_range')
            return
        delta = now - self.last_sample if self.last_sample is not None else 0
        if delta < 0 or delta > self.max_gap:
            self.samples.clear()
            self.recent_voltage.clear()
            self.filtered_voltage = None
            delta = 0
        self.last_sample = now
        self.samples.append((now, voltage))
        self.recent_voltage.append(voltage)
        cutoff = now - self.stable_seconds
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()
        median_voltage = statistics.median(self.recent_voltage)
        if self.filtered_voltage is None:
            self.filtered_voltage = median_voltage
        else:
            alpha = 1 - math.exp(-delta / self.filter_seconds)
            self.filtered_voltage += alpha * (median_voltage - self.filtered_voltage)
        stable = (len(self.samples) >= 5 and now - self.samples[0][0] >= self.stable_seconds
                  and max(v for _, v in self.samples) - min(v for _, v in self.samples) <= self.spread)
        estimate = voltage_soc(self.filtered_voltage, self.curve)
        if self.initialised and self.pending_until is None and self.state in ('invalid', 'stale', 'settling') and not stable:
            self.state = 'settling'
            self.reason = 'waiting_for_stable_voltage'
            return
        if not self.initialised or self.pending_until is not None:
            if not stable:
                self.state = 'settling'
                self.reason = 'waiting_for_stable_voltage'
                return
            self.soc = estimate
            self.ah_used = 0.0
            self.initialised = True
            self.state = 'completed' if self.pending_until is not None else 'ready'
            self.reason = None
            self.pending_until = None
        else:
            # A load step must not immediately look like a battery replacement.
            limit = 0.5 * delta
            self.soc += max(-limit, min(limit, estimate - self.soc))
            if self.state in ('invalid', 'stale', 'settling'):
                self.state = 'ready'
                self.reason = None
        if finite_number(current) and delta:
            self.ah_used += max(0, current) * delta / 3600

    def snapshot(self):
        usable = self.initialised and self.pending_until is None and self.state in ('ready', 'completed')
        return {'state': self.state, 'reason': self.reason, 'request_id': self.request_id,
                'soc_pct': round(self.soc, 1) if usable else None,
                'voltage_v': round(self.filtered_voltage, 3) if self.filtered_voltage is not None else None,
                'quality': self.quality if usable else 'unavailable',
                'ah_used_since_reset': round(self.ah_used, 4),
                'settle_timeout_seconds': self.settle_timeout,
                'profile': 'liion_5s_reference', 'method': 'filtered_voltage_curve'}
