// Author's portfolio, linked from the sign-in page and the sidebar.
export const PORTFOLIO_URL = "https://a-iportfolio-sigma.vercel.app/";

// Shared claim-field definitions for every form (organization claim form,
// customer incident form, new-customer policy form).
//
// OPTIONS mirrors agents/fraud_node.py's ENCODINGS exactly: these are the ONLY
// values the model was trained on. Anything else is routed to manual review.
export const OPTIONS = {
  policy_state: ["IL", "IN", "OH"],
  policy_csl: ["100/300", "250/500", "500/1000"],
  insured_sex: ["FEMALE", "MALE"],
  insured_education_level: ["Associate", "College", "High School", "JD", "MD", "Masters", "PhD"],
  insured_occupation: [
    "adm-clerical", "armed-forces", "craft-repair", "exec-managerial", "farming-fishing",
    "handlers-cleaners", "machine-op-inspct", "other-service", "priv-house-serv",
    "prof-specialty", "protective-serv", "sales", "tech-support", "transport-moving",
  ],
  insured_relationship: ["husband", "not-in-family", "other-relative", "own-child", "unmarried", "wife"],
  incident_type: ["Multi-vehicle Collision", "Parked Car", "Single Vehicle Collision", "Vehicle Theft"],
  collision_type: ["Front Collision", "Not Applicable", "Rear Collision", "Side Collision"],
  incident_severity: ["Major Damage", "Minor Damage", "Total Loss", "Trivial Damage"],
  authorities_contacted: ["Ambulance", "Fire", "None", "Other", "Police"],
  incident_state: ["NC", "NY", "OH", "PA", "SC", "VA", "WV"],
  incident_city: ["Arlington", "Columbus", "Hillsdale", "Northbend", "Northbrook", "Riverwood", "Springfield"],
  property_damage: ["NO", "Unknown", "YES"],
  police_report_available: ["NO", "Unknown", "YES"],
  auto_make: [
    "Accura", "Audi", "BMW", "Chevrolet", "Dodge", "Ford", "Honda", "Jeep",
    "Mercedes", "Nissan", "Saab", "Suburu", "Toyota", "Volkswagen",
  ],
  auto_model: [
    "3 Series", "92x", "93", "95", "A3", "A5", "Accord", "C300", "CRV", "Camry", "Civic",
    "Corolla", "E400", "Escape", "F150", "Forrestor", "Fusion", "Grand Cherokee", "Highlander",
    "Impreza", "Jetta", "Legacy", "M5", "MDX", "ML350", "Malibu", "Maxima", "Neon", "Passat",
    "Pathfinder", "RAM", "RSX", "Silverado", "TL", "Tahoe", "Ultima", "Wrangler", "X5", "X6",
  ],
};

export const DEFAULT_CLAIM = {
  months_as_customer: 200,
  age: 40,
  policy_state: "OH",
  policy_csl: "250/500",
  policy_deductable: 1000,
  policy_annual_premium: 1250,
  umbrella_limit: 0,
  insured_sex: "MALE",
  insured_education_level: "College",
  insured_occupation: "craft-repair",
  insured_relationship: "husband",
  "capital-gains": 0,
  "capital-loss": 0,
  incident_type: "Single Vehicle Collision",
  collision_type: "Side Collision",
  incident_severity: "Minor Damage",
  authorities_contacted: "Police",
  incident_state: "OH",
  incident_city: "Columbus",
  incident_hour_of_the_day: 12,
  number_of_vehicles_involved: 1,
  property_damage: "NO",
  bodily_injuries: 0,
  witnesses: 1,
  police_report_available: "YES",
  total_claim_amount: 30000,
  injury_claim: 3000,
  property_claim: 6000,
  vehicle_claim: 21000,
  auto_make: "Honda",
  auto_model: "Civic",
  auto_year: 2015,
};

// What the organization records on a customer's account (policy, insured person, vehicle).
export const PROFILE_FIELDS = [
  "months_as_customer", "age", "policy_state", "policy_csl", "policy_deductable",
  "policy_annual_premium", "umbrella_limit", "insured_sex", "insured_education_level",
  "insured_occupation", "insured_relationship", "capital-gains", "capital-loss",
  "auto_make", "auto_model", "auto_year",
];

// What a customer fills in when filing a claim (the server computes the total).
export const INCIDENT_FIELDS = [
  "incident_type", "collision_type", "incident_severity", "authorities_contacted",
  "incident_state", "incident_city", "incident_hour_of_the_day", "number_of_vehicles_involved",
  "property_damage", "bodily_injuries", "witnesses", "police_report_available",
  "injury_claim", "property_claim", "vehicle_claim",
];

export const NUMERIC_FIELDS = new Set([
  "months_as_customer", "age", "policy_deductable", "policy_annual_premium", "umbrella_limit",
  "capital-gains", "capital-loss", "incident_hour_of_the_day", "number_of_vehicles_involved",
  "bodily_injuries", "witnesses", "total_claim_amount", "injury_claim", "property_claim",
  "vehicle_claim", "auto_year",
]);

const FIELD_LABELS = {
  "capital-gains": "Capital Gains",
  "capital-loss": "Capital Loss",
  policy_csl: "Policy Coverage Limit (CSL)",
  policy_deductable: "Policy Deductible ($)",
  policy_annual_premium: "Annual Premium ($)",
  umbrella_limit: "Umbrella Limit ($)",
  incident_hour_of_the_day: "Hour of Incident (0-23)",
  number_of_vehicles_involved: "Vehicles Involved",
  property_damage: "Property Damaged?",
  police_report_available: "Police Report Available?",
  injury_claim: "Injury Amount ($)",
  property_claim: "Property Amount ($)",
  vehicle_claim: "Vehicle Amount ($)",
  total_claim_amount: "Total Claim Amount ($)",
};

export function labelFor(field) {
  return FIELD_LABELS[field] || field.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function pick(obj, fields) {
  return Object.fromEntries(fields.map((f) => [f, obj[f]]));
}
