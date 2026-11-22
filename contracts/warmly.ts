// Warmly API contract, version 1. Mirrors the Python dataclasses in warmly/model/api.py and
// warmly/model/types.py (DESIGN-predictive-model.md §4.11).
// Server: Flask server.py on http://localhost:5000. All datetimes are ISO-8601 strings with offset.
// If you change a field here, change the Python dataclass in the same commit (test_contract_keys checks it).

export type ISODateTime = string; // e.g. "2026-11-22T18:45:00+00:00"
export type ISODate = string;     // e.g. "2026-11-22"
export type Room = "living" | "bedroom";
export type Colour = "green" | "amber" | "red" | "grey";
export type Scenario = "off" | "hold_target_overnight" | "warm_1h_before_bed" | "as_scheduled";
export type Point = [ISODateTime, number];               // [time, °C]
export type MaybePoint = [ISODateTime, number | null];   // null = no reading

export interface OvernightPrediction {
  home_id: string;
  room: Room;
  predicted_min_c: number;
  predicted_min_at: ISODateTime;
  predicted_at_7am_c: number;
  hours_below_18: number;
  will_breach_18: boolean;
  curve: Point[];
  assumed_start: boolean;   // true = no real reading, estimate only
  scenario: Scenario;
}

export interface NightOutlook {
  night_of: ISODate;
  days_ahead: number;
  predicted_min_c: number;      // heating-off case
  range_c: [number, number];    // [low, high]
  confidence: "likely" | "possible";
  snow: boolean;
  freezing: boolean;
}

export interface CostCard {
  heat_room_tonight_p: number;  // pence, unrounded
  fan_heater_p: number;         // same period
  blanket_per_hour_p: number;
  summary_text: string;         // already rounded for display
  blanket_tonight_p: number;    // added in the build: the blanket for the same period
  whole_house_per_hour_p: number | null;  // added in the build: whole house, gas, per hour
}

export interface RoomState {
  room: Room;
  temp_c: number | null;              // null = no reading
  last_seen: ISODateTime | null;
  colour: Colour;
  heating_on: boolean | null;         // null = can't tell
  crosses_18_at: ISODateTime | null;  // "below 18°C at ~18:45 if heating stays off"
  predicted_curve: Point[];           // heating-off, next 12 h: draw dashed
  history: MaybePoint[];              // last 24 h: draw solid
}

export interface HomeState {
  home_id: string;
  name: string;
  area: string;
  rooms: RoomState[];
  tonight: OvernightPrediction;
  week: NightOutlook[];               // only nights the forecast reaches
  cost_card: CostCard;
  out_now_c: number;
  out_low_tonight_c: number;
  snow_tonight: boolean;
  status_text: string;                // one line for the home card
}

export interface ClockState {
  running: boolean;
  paused_for_message: boolean;
  speed: number;                      // 720 = 1 simulated hour per 5 real seconds
}

// GET /api/dashboard  (poll every 1 s)
export interface DashboardResponse {
  api_version: "1";
  sim_time: ISODateTime;
  clock: ClockState;
  homes: HomeState[];
}

export interface LoggedMessage {
  at: ISODateTime;
  to: "person" | "contact";
  template_id: string;
  text: string;
  direction: "out" | "in";
}

// GET /api/homes/:home_id
export interface HomeDetailResponse extends HomeState {
  messages: LoggedMessage[];
}

// GET /api/messages  (returns OutgoingMessage[])
export interface OutgoingMessage {
  home_id: string;
  to: "person" | "contact";
  template_id: string;
  text: string;
  reason: string;
}

// POST /api/clock
export interface ClockRequest {
  action: "play" | "pause" | "resume" | "reset" | "jump";
  to?: ISODateTime;   // for "jump"
  speed?: number;
}

// POST /api/clock returns this
export interface ClockResponse {
  sim_time: ISODateTime;
  clock: ClockState;
}

// POST /api/reply  (on-screen phone; same handling as a real SMS reply)
export interface ReplyRequest {
  home_id: string;
  text: string;       // "1" | "2" | "HELP" | "OK" | free text
}

// POST /api/reply returns this
export interface ReplyResponse {
  home_id: string;
  messages: LoggedMessage[];
}

// GET /api/health
export interface HealthResponse {
  ok: boolean;
  forecast_source: "live" | "cache" | "fallback" | "cold_night";
}

// ---- Sign-up and settings (live mode; in demo mode a sign-up is saved for later) ----

export type PropertyType = "mid_terrace" | "semi" | "flat" | "detached" | "bungalow";

// POST /api/signup/lookup  (only after the person or family agrees: consent must be true)
export interface EpcLookupRequest {
  postcode: string;
  address_line?: string;   // e.g. "37 Example Road"; used to pre-select the match
  consent: true;
}

export interface EpcCandidate {
  address: string;
  certificate_number: string;
  band: string;            // "A".."G"
  registered: ISODate;
}

export interface EpcLookupResponse {
  candidates: EpcCandidate[];   // every certificate at the postcode, newest first
  matching: EpcCandidate[];     // the ones matching address_line (exactly 1 = a clear match)
}

export interface SensorSignUp {
  id: string;                   // the sensor_id the gateway posts
  type: "switchbot" | "esp32";
  room: Room;
}

// POST /api/homes  -> 201 SignUpResponse
export interface SignUpRequest {
  name: string;
  age: number;
  area: string;
  postcode?: string;
  address_line?: string;
  property_type?: PropertyType; // needed unless the certificate lookup finds it
  sap_score?: number;           // 1-100, from the certificate ("don't know" = leave out)
  epc_band?: string;            // "A".."G", used if sap_score is missing
  floor_area_m2?: number;
  epc_consent?: boolean;        // true = look the certificate up by postcode + address_line
  sensors?: SensorSignUp[];
  person_phone?: string;        // E.164, e.g. "+447700900001"
  contact_name?: string;
  contact_phone?: string;
  relationship_word?: string;   // what the family calls them, e.g. "Mum"
  night_alerts_opted_in?: boolean;
}

export interface SignUpResponse {
  home_id: string;
  rating_from: "form" | "epc_lookup" | "default";
  certificate_is_old: boolean;  // over 10 years: ask "has the home been insulated since?"
  placement_text: string;       // show this on the sensor step
  notes: string[];              // show these to the person signing up
  live: boolean;                // false = saved, will appear when the server runs in live mode
  record: Record<string, unknown>;
}

// POST /api/homes/:home_id/settings  (send only the fields that change)
export interface SettingsRequest {
  contact_name?: string;
  relationship_word?: string;
  night_alerts_opted_in?: boolean;
  person_phone?: string;
  contact_phone?: string;
}

export interface SettingsResponse {
  contact_name: string | null;
  relationship_word: string | null;
  night_alerts_opted_in: boolean | null;
  phones: { person?: string; contact?: string };
}

export const API_BASE = "http://localhost:5000";
