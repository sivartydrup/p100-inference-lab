"""Pure profile construction for the measured two-client Scream recipe.

No service/configuration writes, credential creation or model downloads.
"""
import copy
from pathlib import Path

ENGINE_ID = 'scream-two-agent'
ALIAS = 'scream-flash-next-iq3-s-two-agent'
PORT = 8088
LABEL = 'Flash Next Two Agents — Queued 256K'
SERVING_PROFILE = dict(alias=ALIAS, quantization='IQ3_S', context=262144,
                       max_output=8192, reasoning=True, vision=True,
                       generation_slots=1, conversation_cache_mib=12288,
                       client_default_thinking=False)


def build_two_agent_profile(base, destination, key):
    """Derive a distinct private profile without modifying its admitted base."""
    if not isinstance(key, str) or not 32 <= len(key) <= 512 or any(c.isspace() for c in key) or key == base.get('api_key'):
        raise ValueError('Use a distinct private upstream key')
    if not Path(destination).is_absolute():
        raise ValueError('Profile directory must be absolute')
    cfg = copy.deepcopy(base)
    args = cfg['args']
    if not isinstance(args, list) or '--slots' in args or '--batch-groups' in args:
        raise ValueError('Review conflicting slot policy before constructing a profile')
    for name, value in [('--spec', 4), ('--lookup', 15)]:
        if args.count(name) != 1 or args.index(name)+1 >= len(args) or str(args[args.index(name)+1]) != str(value):
            raise ValueError('The admitted base differs from the tested speculation recipe')
    for name, value in [('--batch', 1), ('--max-context', 262144), ('--conversation-cache-mib', 12288)]:
        if args.count(name) != 1 or args.index(name)+1 >= len(args):
            raise ValueError('Expected one '+name+' value')
        args[args.index(name)+1] = str(value)
    env = cfg['env']
    retained = {'FNX_PF_STAGE_ROWS':'256', 'FNX_PF_STAGE_ALL':'1',
                'FNX_PF_STAGE_MIB':'1024', 'FNX_PF_TAIL_MIB':'3000', 'FNX_ROUTE_PRIOR':'0'}
    if any(str(env.get(k)) != v for k,v in retained.items()):
        raise ValueError('The admitted base differs from the tested prefill/routing recipe')
    env.update(FNX_EXPERT_RAM_GIB='34.0', FNX_PLE_RAM='0')
    for name in ('FNX_EXPERT_DYN_GIB', 'FNX_ROPE_SCALE', 'FNX_YARN_ORIG_CTX'):
        env.pop(name, None)
    cfg.update(model_name=ALIAS, port=PORT, api_key=key,
               log=str(Path(destination)/'logs/engine.log'))
    return cfg
