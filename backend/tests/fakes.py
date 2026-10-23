import numpy as np


class FakeStream:
    def __init__(self):
        self.samples = 0
        self.decoded = 0
        self.calls = 0

    def accept_waveform(self, sample_rate, samples):
        assert sample_rate == 16000 and samples.dtype == np.float32
        self.samples += len(samples)
        self.calls += 1


class FakeRecognizer:
    """One 'word' per 3200 samples (0.2 s), so captions grow as audio arrives."""

    def __init__(self):
        self.streams: list[FakeStream] = []

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, s):
        return s.decoded < s.samples // 3200

    def decode_stream(self, s):
        s.decoded += 1

    def get_result(self, s):
        return " ".join(f"WORD{i}" for i in range(s.decoded))