import cv2
import numpy as np
import onnxruntime
import json
import os
import argparse
import statistics
from scipy.special import softmax


def read_vocab(path):
    """
    加载词典
    """
    with open(path, encoding="utf-8") as f:
        vocab = json.load(f)
    return vocab


def do_norm(x):
    mean = [0.5, 0.5, 0.5]
    std = [0.5, 0.5, 0.5]
    x = x / 255.0
    x[0, :, :] -= mean[0]
    x[1, :, :] -= mean[1]
    x[2, :, :] -= mean[2]
    x[0, :, :] /= std[0]
    x[1, :, :] /= std[1]
    x[2, :, :] /= std[2]
    return x


def decode_text(tokens, vocab, vocab_inp):
    """
    decode trocr
    """
    s_start = vocab.get('<s>')
    s_end = vocab.get('</s>')
    unk = vocab.get('<unk>')
    pad = vocab.get('<pad>')
    text = ''
    for tk in tokens:

        if tk == s_end:
            break
        if tk not in [s_end, s_start, pad, unk]:
            text += vocab_inp[tk]

    return text


class OnnxEncoder(object):
    def __init__(self, model_path):
        self.model = onnxruntime.InferenceSession(model_path, providers=onnxruntime.get_available_providers())

    def __call__(self, image):
        onnx_inputs = {self.model.get_inputs()[0].name: np.asarray(image, dtype='float32')}
        onnx_output = self.model.run(None, onnx_inputs)[0]
        return onnx_output


class OnnxDecoder(object):
    def __init__(self, model_path):
        self.model = onnxruntime.InferenceSession(model_path, providers=onnxruntime.get_available_providers())
        self.input_names = {input_key.name: idx for idx, input_key in enumerate(self.model.get_inputs())}

    def __call__(self, input_ids,
                 encoder_hidden_states,
                 attention_mask):
        input_info = {"input_ids": input_ids,
                      "attention_mask": attention_mask,
                      "encoder_hidden_states": encoder_hidden_states}
        # 兼容不同版本的模型输入 todo 未来统一模型输入值
        onnx_inputs = {key: input_info[key] for key in self.input_names}
        onnx_output = self.model.run(['logits'], onnx_inputs)
        return onnx_output


class OnnxEncoderDecoder(object):
    def __init__(self, model_path):
        self.encoder = OnnxEncoder(os.path.join(model_path, "encoder_model.onnx"))
        self.decoder = OnnxDecoder(os.path.join(model_path, "decoder_model.onnx"))
        self.vocab = read_vocab(os.path.join(model_path, "vocab.json"))
        self.vocab_inp = {self.vocab[key]: key for key in self.vocab}
        self.threshold = 0.88  # 置信度阈值，由于为进行负样本训练，该阈值较高
        self.max_len = 50  # 最长文本长度

    def run(self, image):
        """
        rgb:image
        """
        image = cv2.resize(image, (384, 384))
        pixel_values = cv2.split(np.array(image))
        pixel_values = do_norm(np.array(pixel_values))
        pixel_values = np.array([pixel_values])
        encoder_output = self.encoder(pixel_values)
        ids = [self.vocab["<s>"], ]
        mask = [1, ]
        scores = []
        for i in range(self.max_len):
            input_ids = np.array([ids]).astype('int64')
            attention_mask = np.array([mask]).astype('int64')
            decoder_output = self.decoder(input_ids=input_ids,
                                          encoder_hidden_states=encoder_output,
                                          attention_mask=attention_mask
                                          )
            pred = decoder_output[0][0]
            pred = softmax(pred, axis=1)
            max_index = pred.argmax(axis=1)
            if max_index[-1] == self.vocab["</s>"]:
                break
            scores.append(pred[max_index.shape[0] - 1, max_index[-1]])
            ids.append(max_index[-1])
            mask.append(1)
        print("解码单字评分：{}".format(scores))
        print("解码平均评分：{}".format(statistics.mean(scores)))
        if self.threshold < statistics.mean(scores):
            text = decode_text(ids, self.vocab, self.vocab_inp)
        else:
            text = ""
        return text


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='onnx model test')
    parser.add_argument('--model', type=str,
                        help="onnx 模型地址")
    parser.add_argument('--test_img', type=str, help="测试图像")

    args = parser.parse_args()
    model = OnnxEncoderDecoder(args.model)
    img = cv2.imread(args.test_img)
    img = img[..., ::-1]  # BRG to RGB
    res = model.run(img)
    print(res)
