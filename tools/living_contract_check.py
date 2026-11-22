"""Exact legacy source verification with only explicitly reviewed adapter hooks removed."""
import hashlib
import json
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='e707a19b0b98324ffd914a28b08601237cd6aea2'


def normalize(path,text):
    if path.endswith('help_service.py'):
        replacements=[
            ('def commit(self,token,challenge,confirmation,*,_living_reservation=None):','def commit(self,token,challenge,confirmation):'),
            ("            if _living_reservation is not None:\n                if action!='accept':raise ApiError(400,'Reservation adapter is only valid for acceptance')\n                _living_reservation(db,uid,c)\n",''),
            ('            from xblago.domain.living_deeds import guard_help, observe_help\n            guard_help(db,uid,rid,action,args,c)\n',''),
            ('            observe_help(db,rid,action,c)\n','')]
    elif path.endswith('asset_registry.py'):
        replacements=[("            self.owned(db,uid,aid)\n            from xblago.domain.living_deeds import purge_asset\n            purge_asset(db,aid,c);db.execute('DELETE FROM blago_assets WHERE id=?',(aid,))",
                       "            self.owned(db,uid,aid);db.execute('DELETE FROM blago_assets WHERE id=?',(aid,))")]
    else:raise ValueError('Unreviewed source contract')
    for before,after in replacements:
        if text.count(before)!=1:raise ValueError('Missing or duplicated reviewed hook')
        text=text.replace(before,after,1)
    return text


def check():
    pins=json.loads((ROOT/'docs/living-deeds01/LEGACY_CONTRACTS.json').read_text())
    if pins['base']!=BASE:raise ValueError('Baseline substitution')
    for path,digest in pins['sha256'].items():
        original=subprocess.check_output(['git','show',BASE+':'+path],cwd=ROOT)
        if hashlib.sha256(original).hexdigest()!=digest:raise ValueError('Frozen contract pin substitution')
        actual=hashlib.sha256(normalize(path,(ROOT/path).read_text()).encode()).hexdigest()
        if actual!=digest:raise ValueError('Non-adapter legacy change: '+path)
    print('Exact predecessor source plus reviewed Living adapter hooks: PASS')

if __name__=='__main__':check()
