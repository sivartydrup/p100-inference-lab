import type { ServingProfile } from './engines';

export type Recipe = {
  id: string; mode: 'Fast' | 'Reasoning'; expert_cache_mib: number; tensor_split: number[]; draft_tokens: number;
  served_alias: string; client_settings: Record<string, unknown>; notice: string; sha256?: string;
};

export function ohMyPiProfile(recipe: Recipe, host: string, contextWindow: number) {
  const reasoning = recipe.mode === 'Reasoning';
  const longContext = contextWindow > 32768;
  const provider = longContext ? 'p100-full-context' : reasoning ? 'p100-reasoning' : 'p100-fast';
  // JSON is valid YAML. Export a mergeable fragment, never credentials from dashboard state.
  return JSON.stringify({ providers: { [provider]: {
    baseUrl: `http://${host}:8080/v1`, apiKey: 'REPLACE_WITH_INFERENCE_API_KEY', api: 'openai-completions',
    models: [{ id: recipe.served_alias, name: longContext ? 'P100 Flash Next 256K' : `P100 Flash Next ${recipe.mode}`, reasoning, input: ['text'],
      contextWindow, maxTokens: reasoning ? 4096 : 2048,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false,
        maxTokensField: 'max_tokens', extraBody: recipe.client_settings } }],
  } } }, null, 2) + '\n';
}

export function downloadClientProfile(recipe: Recipe, contextWindow: number) {
  download(ohMyPiProfile(recipe, window.location.hostname, contextWindow), recipe.id);
}

export function downloadGatewayProfile(profile: ServingProfile, name: string) {
  const thinking = profile.client_default_thinking;
  const provider = thinking === undefined ? 'p100' : 'p100-' + profile.alias;
  const content = { providers: { [provider]: {
    baseUrl: `http://${window.location.hostname}:8090/v1`, apiKey: 'P100_INFERENCE_KEY', api: 'openai-completions',
    models: [{ id: profile.alias, name, reasoning: thinking ?? profile.reasoning, input: profile.vision ? ['text', 'image'] : ['text'],
      contextWindow: profile.context, maxTokens: profile.max_output,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      ...(thinking === undefined ? {} : { compat: { maxTokensField: 'max_tokens',
        extraBody: { chat_template_kwargs: { enable_thinking: thinking } } } }) }],
    compat: { streamFirstEventTimeoutMs: 1800000 },
  } } };
  download(JSON.stringify(content, null, 2) + '\n', profile.alias);
}

function download(content: string, profileId: string) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/yaml' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `oh-my-pi-${profileId}.models.yml`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
