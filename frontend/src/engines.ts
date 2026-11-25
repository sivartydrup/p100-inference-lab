export const engineNames: Record<string, string> = {
  kmic: 'Kmic',
  'kmic-moe': 'Kmic MoE',
  pxa: 'PXA',
  qr0n: 'qr0n',
  strata: 'Strata',
  scream: 'Scream P100',
  'scream-512k': 'Scream P100 512K',
  inkling: 'Inkling 2 Mini',
};

export type ServingProfile = {
  alias: string;
  quantization: string;
  context: number;
  max_output: number;
  reasoning: boolean;
  vision: boolean;
};
