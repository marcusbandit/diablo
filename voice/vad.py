import numpy as np
import onnxruntime as ort

CONTEXT = 64


class SileroVAD:
    def __init__(self, path, threshold=0.5):
        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(CONTEXT, dtype=np.float32)

    def prob(self, chunk):
        chunk = np.asarray(chunk, dtype=np.float32)
        feed_input = np.concatenate([self.context, chunk])[np.newaxis, :]
        feed = {
            "input": feed_input,
            "state": self.state,
            "sr": np.array(16000, dtype=np.int64),
        }
        outs = self.session.run(None, feed)
        self.state = outs[1].astype(np.float32)
        self.context = feed_input[0, -CONTEXT:]
        return float(np.asarray(outs[0]).reshape(-1)[0])
