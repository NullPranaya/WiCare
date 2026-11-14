import sys; sys.path.insert(0, ".")
import numpy as np
from sklearn.metrics import f1_score, balanced_accuracy_score, recall_score, precision_score
from ml.features import clip_features
from ml.train_rf import loso
d=np.load('data/clips.npz',allow_pickle=True); X,y,g=d['X'],d['y'],d['groups']
F=np.stack([clip_features(x) for x in X])
for name, yy in [('fall_vs_rest',(y==0).astype(int)), ('3way_fall_walk_other', np.select([y==0,y==4],[0,2],1))]:
    folds,yt,yp=loso(F,yy,g)
    k=len(set(yy))
    print(name, 'bal_acc',round(balanced_accuracy_score(yt,yp),3),'macroF1',round(f1_score(yt,yp,average='macro'),3),
          'recall per class',np.round(recall_score(yt,yp,average=None),2),'precision',np.round(precision_score(yt,yp,average=None),2))
