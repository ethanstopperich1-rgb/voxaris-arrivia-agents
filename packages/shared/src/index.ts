export type QualificationGate =
  | "age_25_plus"
  | "income_50k_plus"
  | "decision_makers_present"
  | "valid_credit_card"
  | "no_recent_tour"
  | "residency_outside_market";

export const QUALIFICATION_GATES: readonly QualificationGate[] = [
  "age_25_plus",
  "income_50k_plus",
  "decision_makers_present",
  "valid_credit_card",
  "no_recent_tour",
  "residency_outside_market",
] as const;

export type CallEndReason =
  | "qualified_and_booked"
  | "disqualified_age"
  | "disqualified_income"
  | "disqualified_decision_makers"
  | "disqualified_no_card"
  | "disqualified_recent_tour"
  | "disqualified_residency"
  | "voicemail"
  | "transferred_to_human"
  | "caller_hangup"
  | "agent_error"
  | "deposit_failed";

export const DEPOSIT_AMOUNT_CENTS = 7500 as const;
