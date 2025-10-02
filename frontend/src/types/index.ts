export type InvestorRole = 'lead' | 'non_lead' | 'stealth';

export type InvestmentStage =
  | 'pre_seed'
  | 'seed'
  | 'series_a'
  | 'series_b'
  | 'series_c'
  | 'series_d'
  | 'growth'
  | 'exit';

export interface Deal {
  id: string;
  startupName: string;
  investorRoles: InvestorRole[]; // For the dot indicators
  investmentStage: InvestmentStage;
  amountInvested: string;
  date: string;
  nextSteps?: string;
}

export interface Fund {
  slug: string;
  name: string;
  ingestionUrl: string;
  scraperType: string;
}
