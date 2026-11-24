"""02.2 evidence contract. Pure data, no application/storage/browser imports."""
import hashlib
import json

SCHEMA = 'blago-living-bundle-1'
HARNESS = 'living-preparation-0.1'
VERIFIER = 'living-offline-verifier-1'
BASE = '2e6e5aab1a2995f6d6c157bf35029f21e834df7b'
BANNER = 'LOCAL SANDBOX · SYNTHETIC DATA · NO EXTERNAL DELIVERY'
ROLES = ('A', 'B', 'C')
SURFACES = ('visible', 'full_dom', 'network_response')
BRANCHES = ('decline', 'revoke', 'consent')
REASON = 'N/A — причина не собирается'
COMMON = ('initial', 'role_binding_probes', 'author_forward_permission', 'match_private', 'memory_search',
          'intermediary_preview', 'intermediary_send', 'recipient_inbox')
TAIL = {
    'decline': ('recipient_receive', 'recipient_decline', 'restart_cleanup_relogin',
                'new_match_private', 'new_memory_search', 'new_preview', 'new_attempt_blocked'),
    'revoke': ('author_revoke', 'recipient_open_denied'),
    'consent': ('recipient_receive', 'contact_consent', 'offer_disclosure_preview',
                'offer_help', 'author_accept', 'helper_confirm', 'author_confirm'),
}
CHECKPOINTS = {'author_forward_permission': 'CP1', 'intermediary_preview': 'CP2',
               'intermediary_send': 'CP3'}
ACTORS = {'initial': None, 'role_binding_probes': None, 'author_forward_permission': 'A', 'match_private': 'B',
          'memory_search': 'B', 'intermediary_preview': 'B', 'intermediary_send': 'B',
          'recipient_inbox': 'C', 'recipient_receive': 'C', 'recipient_decline': 'C',
          'restart_cleanup_relogin': None, 'new_match_private': 'B', 'new_memory_search': 'B',
          'new_preview': 'B', 'new_attempt_blocked': 'B', 'author_revoke': 'A',
          'recipient_open_denied': 'C', 'contact_consent': 'C', 'offer_disclosure_preview': 'C',
          'offer_help': 'C', 'author_accept': 'A', 'helper_confirm': 'C', 'author_confirm': 'A'}
DELIVERY_FIELDS = ('format', 'receipt', 'task', 'referrer', 'contact_only', 'resource_available')


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(value): return hashlib.sha256(value).hexdigest()


def steps(branch, audience_change=False):
    result = list(COMMON + TAIL[branch])
    if audience_change and branch == 'consent':
        i = result.index('offer_help')
        result[i:i] = ['author_audience_change', 'author_republish']
    return result


def expected_matrix(*, audience_change=False):
    """Freeze BEFORE observation. Optional owner steps require a new plan hash."""
    rows = []
    for branch in BRANCHES:
        plan = steps(branch, audience_change)
        for index, step in enumerate(plan):
            received = 'recipient_receive' in plan[:index+1]
            reset = branch == 'decline' and 'restart_cleanup_relogin' in plan[:index+1]
            declined = step == 'recipient_decline'
            note_seen = index >= plan.index('memory_search') and not reset
            if reset: note_seen = index >= plan.index('new_memory_search')
            identity_disclosed = branch == 'consent' and index >= plan.index(
                'author_audience_change' if audience_change else 'offer_help')
            for role in ROLES:
                for surface in SURFACES:
                    note = ('REQUIRED' if role == 'B' and
                            ((surface != 'network_response' and note_seen) or
                             (surface == 'network_response' and step in ('memory_search', 'new_memory_search')))
                            else 'FORBIDDEN')
                    if role in ('A','B'):
                        request = 'REQUIRED'
                    elif surface == 'network_response':
                        request = 'REQUIRED' if step == 'recipient_receive' else 'FORBIDDEN'
                        if audience_change and received and branch == 'consent': request = 'OPTIONAL'
                    else:
                        request = 'REQUIRED' if received and not reset else 'FORBIDDEN'
                        if declined and surface == 'visible': request = 'FORBIDDEN'
                    cid = ('REQUIRED' if role == 'A' and identity_disclosed else
                           'FORBIDDEN' if role == 'A' else 'OPTIONAL')
                    rows.append({'branch':branch, 'checkpoint':CHECKPOINTS.get(step, step),
                        'step':step, 'role':role, 'surface':surface, 'banner_required':surface!='network_response',
                        'markers':{'NOTE_SECRET':note,'RECIPIENT_ID_C':cid,'REQ_TEXT':request},
                        'full_request_required':request=='REQUIRED',
                        'note_mask_required':role=='B' and note=='REQUIRED',
                        'counters_zero_required':branch=='consent' and step=='contact_consent',
                        'refusal_reason':REASON})
    return rows


def key(row): return tuple(row[k] for k in ('branch','step','role','surface'))


def redact_note(text, full_note, marker):
    """Scan first; never add a positive control that was not really observed.

    This helper is for textual fields. It is NOT a screenshot/DOM collector.
    Screenshot redaction must preserve the real marker pixels and mask the rest.
    """
    if not full_note or marker not in full_note: raise ValueError('Private note context required')
    observed = marker in text
    if observed and full_note not in text: raise ValueError('Unknown note encoding: do not persist')
    output = text.replace(full_note, marker+' [MASKED]') if full_note else text
    return output, {'marker_seen_before_mask':observed, 'full_note_persisted':bool(full_note and full_note in output)}
