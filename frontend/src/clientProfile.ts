export type Recipe = {
  id: string; mode: 'Fast' | 'Reasoning'; expert_cache_mib: number; tensor_split: number[]; draft_tokens: number;
  served_alias: string; client_settings: Record<string, unknown>; notice: string; sha256?: string;
};

export function ohMyPiProfile(recipe: Recipe, host: string) {
  const reasoning = recipe.mode === 'Reasoning';
  // JSON is valid YAML. Export a mergeable fragment, never credentials from dashboard state.
  return JSON.stringify({ providers: { [reasoning ? 'p100-reasoning' : 'p100-fast']: {
    baseUrl: `http://${host}:8080/v1`, apiKey: 'REPLACE_WITH_INFERENCE_API_KEY', api: 'openai-completions',
    models: [{ id: recipe.served_alias, name: `P100 Flash Next ${recipe.mode}`, reasoning, input: ['text'],
      contextWindow: 32768, maxTokens: reasoning ? 4096 : 2048,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false,
        maxTokensField: 'max_tokens', extraBody: recipe.client_settings } }],
  } } }, null, 2) + '\n';
}

export function downloadClientProfile(recipe: Recipe) {
  const url = URL.createObjectURL(new Blob([ohMyPiProfile(recipe, window.location.hostname)], { type: 'text/yaml' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `oh-my-pi-${recipe.id}.models.yml`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
