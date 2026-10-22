"""Physics helpers and the kite-jump trajectory model.

Key facts used throughout (see docs/PLAN.md section 4):

* A kite jump is NOT ballistic.  During the flight the kite keeps pulling on the
  rider (line tension T), so the net vertical acceleration is  a_net = g - T_z/m,
  typically 2-4 m/s^2 instead of 9.81.  Consequences:
    - the wrist accelerometer reads |f| = T/m ~ 0.5-0.9 g in the air, not 0 g
      (a free-fall detector would miss every kite jump);
    - airtime is much longer than sqrt(8h/g):  28.6 m / 8.5 s (Jacobsen 2017),
      32.2 m / 10.15 s (Whaley 2023) correspond to a_net ~ 2.5-3.2 m/s^2.
* Barometric height:  dP/dh = -rho*g ~ -12 Pa/m at sea level, 15 C.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

G = 9.80665
R_AIR = 287.05
T0_K = 288.15
RHO0 = 1.225
PA_PER_M = RHO0 * G           # 12.01 Pa/m at sea level, 15 C


def rho_from_pressure(p_pa: float, temp_c: float = 15.0) -> float:
    return p_pa / (R_AIR * (temp_c + 273.15))


def pressure_to_rel_height(p_pa: np.ndarray, p_ref_pa: np.ndarray | float, temp_c: float = 15.0) -> np.ndarray:
    """Height above the reference pressure level (m), linearised (fine for < 100 m)."""
    rho = rho_from_pressure(np.mean(np.atleast_1d(p_ref_pa)), temp_c)
    return (np.asarray(p_ref_pa, float) - np.asarray(p_pa, float)) / (rho * G)


def dynamic_pressure(v_mps: float, rho: float = RHO0) -> float:
    """1/2 rho v^2 - the Bernoulli term a moving pressure port may partly see."""
    return 0.5 * rho * v_mps * v_mps


@dataclass
class JumpModel:
    """Piecewise-constant net acceleration model of a kite jump.

    ascent : h(t) = v0 t - 1/2 a_up t^2           0 <= t <= t_up = v0/a_up
    descent: h(t) = h_max - 1/2 a_dn (t - t_up)^2 t_up <= t <= T
    with h_max = v0^2 / (2 a_up),  t_dn = sqrt(2 h_max / a_dn).
    """
    v0: float
    a_up: float
    a_dn: float

    @property
    def h_max(self) -> float:
        return self.v0 ** 2 / (2 * self.a_up)

    @property
    def t_up(self) -> float:
        return self.v0 / self.a_up

    @property
    def t_dn(self) -> float:
        return math.sqrt(2 * self.h_max / self.a_dn)

    @property
    def airtime(self) -> float:
        return self.t_up + self.t_dn

    @property
    def v_land(self) -> float:
        return self.a_dn * self.t_dn

    def h(self, tau):
        tau = np.asarray(tau, float)
        up = self.v0 * tau - 0.5 * self.a_up * tau ** 2
        dn = self.h_max - 0.5 * self.a_dn * (tau - self.t_up) ** 2
        out = np.where(tau <= self.t_up, up, dn)
        return np.clip(out, 0.0, None)

    def vz(self, tau):
        tau = np.asarray(tau, float)
        return np.where(tau <= self.t_up, self.v0 - self.a_up * tau, -self.a_dn * (tau - self.t_up))

    def a_net(self, tau):
        """Rider vertical acceleration (negative = downward)."""
        tau = np.asarray(tau, float)
        return np.where(tau <= self.t_up, -self.a_up, -self.a_dn)

    @classmethod
    def from_height(cls, h_max: float, a_up: float, a_dn: float) -> "JumpModel":
        return cls(v0=math.sqrt(2 * a_up * h_max), a_up=a_up, a_dn=a_dn)

    @classmethod
    def from_airtime(cls, airtime: float, a_up: float, a_dn: float) -> "JumpModel":
        v0 = airtime / (1.0 / a_up + 1.0 / math.sqrt(a_up * a_dn))
        return cls(v0=v0, a_up=a_up, a_dn=a_dn)


def height_from_airtime(airtime: float, a_eff: float = 3.2) -> float:
    """Symmetric-model fallback h = a_eff T^2 / 8.  a_eff ~ 3.2 m/s^2 reproduces
    the 28.6 m / 8.5 s reference jump; calibrate per rider from baro-validated jumps."""
    return a_eff * airtime ** 2 / 8.0


def ballistic_height(airtime: float) -> float:
    """Upper bound: no kite lift at all."""
    return G * airtime ** 2 / 8.0
