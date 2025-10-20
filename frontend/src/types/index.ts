export type InvestorRole = 'lead' | 'non_lead' | 'stealth';

export type InvestmentStage =
  | 'pre_seed'
  | 'seed'
  | 'series_a'
  | 'series_b'
  | 'series_c'
  | 'series_d'
  | 'growth'
  | 'exit'
  | 'unknown';

export type EnterpriseCategory =
  | 'infrastructure'
  | 'security'
  | 'vertical_saas'
  | 'agentic'
  | 'data_intelligence'
  | 'not_enterprise';

export interface Deal {
  id: string;
  startupName: string;
  investorRoles: InvestorRole[]; // For the dot indicators
  investmentStage: InvestmentStage;
  amountInvested: string;
  date: string;
  nextSteps?: string;
  // Enterprise AI fields
  enterpriseCategory?: EnterpriseCategory;
  isEnterpriseAi: boolean;
  leadInvestor?: string;
  leadPartner?: string;
  verificationSnippet?: string;
}

export interface Fund {
  slug: string;
  name: string;
  ingestionUrl: string;
  scraperType: string;
}
