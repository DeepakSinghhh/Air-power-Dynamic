import numpy as np

from sarthi import met
from sarthi.events import apply_events
from sarthi.scenario import generate


def _hourly(n=30, rh=60.0, t=15.0, td=7.0, wind=12.0, low=0.0, tot=0.0, start="2026-01-01T00:00"):
    base = np.datetime64(start)
    times = [str(base + np.timedelta64(i, "h"))[:16] for i in range(n)]
    return {"time": times, "relative_humidity_2m": [rh] * n, "temperature_2m": [t] * n,
            "dew_point_2m": [td] * n, "wind_speed_10m": [wind] * n, "cloud_cover_low": [low] * n,
            "cloud_cover": [tot] * n, "visibility": [24000.0] * n}


def test_features_shape_and_missing_values():
    h = _hourly()
    h["relative_humidity_2m"][3] = None
    X = met.features(h)
    assert X.shape == (30, len(met.FEATURES))
    assert np.isnan(X[3, 0])


def test_logistic_fit_recovers_separable_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, len(met.FEATURES)))
    y = (X[:, 0] + 0.5 * X[:, 3] > 0.8).astype(float)
    m = met.fit_logistic(X, y, l2=0.1)
    p = met.predict(m, X)
    assert np.mean((p > 0.5) == y) > 0.95


def test_trained_model_responds_to_saturation_and_calm():
    model = met.load_model()
    dry = met.predict(model, met.features(_hourly(rh=55, t=18, td=8, wind=15)))
    foggy = met.predict(model, met.features(_hourly(rh=100, t=8, td=8, wind=3, low=100)))
    assert foggy[5] > 0.5 > dry[5]          # 05:00 local: saturated + calm vs dry + breezy
    assert met.load_model()["meta"]["brier_skill"] > 0


def test_fog_windows_respect_threshold_and_now():
    fc = met.MetForecast(source="t", label="t", date="2026-01-01", bases={
        "X": met.BaseMet(times=[i * 60 for i in range(10)], p_fog=[0.1, 0.6, 0.7, 0.9, 0.4, 0.2, 0.8, 0.8, 0.1, 0.1],
                         nwp_vis_m=[None] * 10, rh=[None] * 10, wind_kmh=[None] * 10)})
    ws = met.fog_windows(fc, 0.5)
    assert [(w.start, w.end, w.peak) for w in ws] == [(60, 239, 0.9), (360, 479, 0.8)]
    later = met.fog_windows(fc, 0.5, after=150)
    assert later[0].start == 150  # never closes the past


def test_snapshot_forecast_is_offline_and_yields_closures():
    world = generate(7)
    fc = met.snapshot_forecast(world)
    assert set(fc.bases) == set(world.bases)
    assert all(len(b.p_fog) == len(b.times) and b.times[0] == 0 for b in fc.bases.values())
    events = met.closure_events(world, met.fog_windows(fc, 0.5), at=0)
    assert events, "the cached dense-fog night should close at least one base at P>=0.5"
    # Approving them makes the same forecast propose nothing new.
    w2, _ = apply_events(world, events)
    assert met.closure_events(w2, met.fog_windows(fc, 0.5), at=0) == []
