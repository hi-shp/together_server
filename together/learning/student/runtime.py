"""NumPy-only inference for the frozen local candidates (no sklearn on the crawler)."""
from collections import Counter
from pathlib import Path
import json, math, re
import numpy as np
from .views import intent, pairtext, focus_features
from .pairs import txt, pairfeat
from .policy_features import reference_policy


def category_text(row):
    policy = reference_policy(row)
    signal = (' ' + policy['rule']) * 5 if policy else ''
    return signal + ' ' + intent(row) + ' ' + row.get('body', '')[:6500]

class Student:
    def __init__(self, directory):
        self.root = Path(directory)
        self.meta = json.loads((self.root / 'student.json').read_text(encoding='utf-8'))
        self.weights = np.load(self.root / 'weights.npz', allow_pickle=False)
        self.cache = {}

    def array(self, key):
        if key not in self.cache:
            self.cache[key] = self.weights[key]
        return self.cache[key]

    def vector(self, name, text):
        meta = self.meta['vectors'][name]
        text = text.lower()
        lo, hi = meta['ngram']
        if meta['analyzer'] == 'char':
            text = re.sub(r'\s\s+', ' ', text)
            counts = Counter(text[i:i+n] for n in range(lo, hi+1) for i in range(len(text)-n+1))
        else:
            counts = Counter(re.findall(r'\S+', text))
        vocabulary = meta['vocabulary']
        present = [(vocabulary[t], c) for t,c in counts.items() if t in vocabulary]
        indices = np.array([x[0] for x in present], dtype=np.int32)
        values = np.array([1+math.log(x[1]) for x in present], dtype=np.float32)
        values *= self.array(name+'_idf')[indices]
        length = float(np.linalg.norm(values))
        if length: values /= length
        return indices, values

    def linear(self, name, text):
        indices, values = self.vector(name, text)
        scores = self.array(name+'_coef')[:,indices] @ values + self.array(name+'_bias')
        return scores

    def category(self, row):
        if self.meta.get('category_mode') == 'mastery':
            from .evidence import purpose
            text = purpose(row) + ' ' + txt(row)
        else:
            text = category_text(row)
        scores = self.linear('category', text)
        order = np.argsort(scores)
        margin = float(scores[order[-1]]-scores[order[-2]])
        probability = np.exp((scores-scores.max())*3);probability /= probability.sum()
        return {'label':self.meta['category_classes'][order[-1]], 'confidence':float(probability.max()), 'margin':margin, 'scores':scores.tolist(), 'model_version':self.meta['version']}

    def forest(self, name, values):
        left,right,feature,threshold,probability,roots = [self.array(name+'_'+key) for key in ('left','right','feature','threshold','value','roots')]
        result = np.zeros(probability.shape[1], dtype=np.float64)
        for root in roots:
            node=int(root)
            while left[node] != -1:
                node=int(left[node] if values[feature[node]] <= threshold[node] else right[node])
            result += probability[node] / max(float(probability[node].sum()),1e-30)
        return result / len(roots)

    def pair_component(self, name, row):
        if name.startswith('focus'):
            vectors=[]
            for notice in [row['left'],row['right']]:
                indices,values=self.vector(name,txt(notice))
                vectors.append(self.array(name+'_svd')[:,indices] @ values)
            features=np.asarray(pairfeat(row,np.asarray(vectors))+focus_features(row),dtype=np.float32)
            return self.forest(name,features)
        scores=self.linear(name,pairtext(row));z=np.exp((scores-scores.max())*3)
        return z/z.sum()

    def pair(self, row):
        if self.meta.get('pair_mode') == 'mastery':
            vectors=[]
            for notice in (row['left'],row['right']):
                indices,values=self.vector('mastery',txt(notice))
                vectors.append(self.array('mastery_svd')[:,indices] @ values)
            scores=self.forest('mastery',np.asarray(pairfeat(row,np.asarray(vectors)),dtype=np.float32))
            return {'label':self.meta['pair_classes'][int(scores.argmax())], 'confidence':float(scores.max()), 'scores':scores.tolist(), 'model_version':self.meta['version']}
        a,b,w=self.meta['pair_selection'];scores=self.pair_component(a,row)*(1-w)+self.pair_component(b,row)*w
        return {'label':self.meta['pair_classes'][int(scores.argmax())], 'confidence':float(scores.max()), 'scores':scores.tolist(), 'model_version':self.meta['version']}

    def close(self):
        self.weights.close()
