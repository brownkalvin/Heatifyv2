// Sign-up: a family member adds the person they look after.
// POST /api/homes (SignUpRequest in contracts/warmly.ts), with the optional
// energy-certificate lookup (POST /api/signup/lookup, only with consent).
import { html, useState, useEffect } from './preact.js';
import { postSignUp, postLookup } from './api.js';
import { Icon } from './icons.js';

const PROPERTY_TYPES = [
  ['flat', 'Flat'],
  ['mid_terrace', 'Terraced house'],
  ['semi', 'Semi-detached house'],
  ['detached', 'Detached house'],
  ['bungalow', 'Bungalow'],
];
const BANDS = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@.][^\s@]*$/;

const EMPTY = {
  name: '', relationship_word: '', age: '', area: '',
  property_type: '', rating: 'unknown', epc_band: '',
  postcode: '', address_line: '', consent: false,
  person_email: '', contact_name: '', contact_email: '', night_alerts_opted_in: false,
};

/** Field errors for the form, keyed by field name. Empty = ready to send. */
function check(f, picked) {
  const e = {};
  const age = Number(f.age);
  if (!f.name.trim()) e.name = 'Enter their first name.';
  if (!f.age.trim()) e.age = 'Enter their age.';
  else if (!Number.isInteger(age) || age < 1 || age > 120) e.age = 'Enter an age in whole years.';
  if (!f.area.trim()) e.area = 'Enter the town or area they live in.';
  if (!f.property_type) e.property_type = 'Choose the kind of home.';
  if (f.rating === 'band' && !f.epc_band) e.epc_band = 'Choose the band, or pick "I don\'t know".';
  if (f.rating === 'lookup' && !picked) e.lookup = 'Find their home and pick it from the list, or pick "I don\'t know".';
  if (!f.contact_name.trim()) e.contact_name = 'Enter your name.';
  if (!EMAIL.test(f.contact_email.trim())) e.contact_email = 'Enter your email address, like name@example.com.';
  if (f.person_email.trim() && !EMAIL.test(f.person_email.trim()))
    e.person_email = 'Check this email address, or leave it empty.';
  return e;
}

function requestBody(f, picked) {
  const body = {
    name: f.name.trim(), age: Number(f.age), area: f.area.trim(),
    property_type: f.property_type,
    relationship_word: f.relationship_word.trim(),
    contact_name: f.contact_name.trim(),
    contact_email: f.contact_email.trim(),
    night_alerts_opted_in: f.night_alerts_opted_in,
  };
  if (f.person_email.trim()) body.person_email = f.person_email.trim();
  if (f.rating === 'band') body.epc_band = f.epc_band;
  if (f.rating === 'lookup' && picked) {
    // The server looks the certificate up again for the full rating; the band
    // they picked is the fallback if that second lookup finds nothing.
    Object.assign(body, { postcode: f.postcode.trim(), address_line: picked.address, epc_consent: true,
      epc_band: picked.band });
  }
  return body;
}

function Field({ id, label, hint, error, children }) {
  return html`<div class=${'field' + (error ? ' invalid' : '')}>
    <label class="field-label" for=${id}>${label}</label>
    ${children}
    ${hint && !error && html`<p class="footnote muted field-hint" id=${id + '-hint'}>${hint}</p>`}
    ${error && html`<p class="footnote field-error" id=${id + '-error'}>${error}</p>`}
  </div>`;
}

function TextInput({ id, form, set, error, hint, ...rest }) {
  const describedBy = error ? id + '-error' : hint ? id + '-hint' : undefined;
  return html`<input class="text-input" id=${id} name=${id} value=${form[id]}
    aria-invalid=${error ? 'true' : undefined} aria-describedby=${describedBy}
    onInput=${(e) => set(id, e.currentTarget.value)} ...${rest} />`;
}

function Choices({ name, legend, options, value, onChange, error }) {
  return html`<fieldset class=${'field choices' + (error ? ' invalid' : '')}>
    <legend class="field-label">${legend}</legend>
    ${options.map(([v, label, sub]) => html`<label class="choice">
      <input type="radio" name=${name} value=${v} checked=${value === v} onChange=${() => onChange(v)} />
      <span class="choice-text"><span>${label}</span>${sub && html`<span class="footnote muted">${sub}</span>`}</span>
    </label>`)}
    ${error && html`<p class="footnote field-error">${error}</p>`}
  </fieldset>`;
}

function Lookup({ form, set, picked, setPicked, error }) {
  const [state, setState] = useState({ busy: false, error: null, candidates: null });
  const find = async () => {
    setPicked(null);
    if (!form.postcode.trim()) return setState({ busy: false, error: 'Enter their postcode.', candidates: null });
    setState({ busy: true, error: null, candidates: null });
    try {
      const res = await postLookup(form.postcode.trim(), form.address_line.trim());
      if (res.matching.length === 1) setPicked(res.matching[0]);
      setState({
        busy: false, candidates: res.candidates,
        error: res.candidates.length ? null : 'No energy certificates found at that postcode.',
      });
    } catch (e) {
      setState({ busy: false, error: `Couldn't look it up: ${e.message}`, candidates: null });
    }
  };
  return html`<div class="lookup">
    <label class="choice consent">
      <input type="checkbox" checked=${form.consent} onChange=${(e) => { set('consent', e.currentTarget.checked); setPicked(null); }} />
      <span class="choice-text">
        <span>They agree to us looking up their home's energy certificate</span>
        <span class="footnote muted">It's on the public register. We only keep the rating and the kind of home.</span>
      </span>
    </label>
    ${form.consent && html`
      <${Field} id="postcode" label="Postcode">
        <${TextInput} id="postcode" form=${form} set=${set} autocomplete="postal-code" autocapitalize="characters" />
      <//>
      <${Field} id="address_line" label="House number and street" hint="Helps us pick the right certificate.">
        <${TextInput} id="address_line" form=${form} set=${set} hint="1" autocomplete="address-line1" />
      <//>
      <button type="button" class="button secondary" disabled=${state.busy} onClick=${find}>
        ${state.busy ? 'Looking…' : 'Find their home'}
      </button>
      ${state.error && html`<p class="footnote field-error" role="alert">${state.error}</p>`}
      ${state.candidates && state.candidates.length > 0 && html`
        <fieldset class="field choices">
          <legend class="field-label">Which one is their home?</legend>
          ${state.candidates.map((c) => html`<label class="choice">
            <input type="radio" name="certificate" checked=${picked && picked.certificate_number === c.certificate_number}
              onChange=${() => setPicked(c)} />
            <span class="choice-text"><span>${c.address}</span>
              <span class="footnote muted">Band ${c.band} · registered ${c.registered}</span></span>
          </label>`)}
        </fieldset>`}`}
    ${error && html`<p class="footnote field-error">${error}</p>`}
  </div>`;
}

function Done({ result, form, onAnother, onClose }) {
  const name = form.name.trim();
  return html`
    <section class="card alert stage-ok" role="status">
      <div class="alert-head">
        <span class="alert-icon">${Icon.check(22)}</span>
        <span class="eyebrow">Signed up</span>
      </div>
      <h2 class="title2">${name} is signed up</h2>
      <p class="body">
        We'll email you at ${form.contact_email.trim()} if ${name}'s home gets too cold.
        ${form.person_email.trim()
          ? ` ${name} will get check-ins at ${form.person_email.trim()}.`
          : ` ${name} won't get emails from us.`}
      </p>
    </section>

    ${result.notes.length > 0 && html`
      <h3 class="section-header">Good to know</h3>
      <ul class="card plain-list">${result.notes.map((n) => html`<li class="body">${n}</li>`)}</ul>`}

    <h3 class="section-header">Next: thermometers</h3>
    <section class="card">
      <p class="body">
        ${result.live
          ? `${name}'s page will fill in once thermometers start sending readings.`
          : `${name}'s home is saved. Their page appears once Heatify is running with real thermometers.`}
      </p>
      <p class="footnote muted field-hint">${result.placement_text}</p>
    </section>

    <div class="form-actions">
      ${result.live
        ? html`<a class="button" href=${`?home=${encodeURIComponent(result.home_id)}`}>Open ${name}'s page</a>`
        : html`<button type="button" class="button" onClick=${onClose}>Done</button>`}
      <button type="button" class="button secondary" onClick=${onAnother}>Sign up someone else</button>
    </div>
  `;
}

export function SignUp({ onClose }) {
  const [form, setForm] = useState(EMPTY);
  const [picked, setPicked] = useState(null); // the chosen energy certificate
  const [errors, setErrors] = useState({});
  const [tried, setTried] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [result, setResult] = useState(null);

  // Functional updates, so quick changes in a row (autofill) don't overwrite each other
  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const pick = setPicked;
  useEffect(() => {
    if (tried) setErrors(check(form, picked)); // once they've tried to send, errors update as they fix them
  }, [form, picked, tried]);

  const submit = async (e) => {
    e.preventDefault();
    setTried(true);
    const found = check(form, picked);
    setErrors(found);
    if (Object.keys(found).length) {
      requestAnimationFrame(() => document.querySelector('.invalid input, .invalid select')?.focus());
      return;
    }
    setSending(true);
    setSendError(null);
    try {
      setResult(await postSignUp(requestBody(form, picked)));
      window.scrollTo(0, 0);
    } catch (err) {
      setSendError(err.message || String(err));
    }
    setSending(false);
  };

  if (result) {
    const again = () => { setForm(EMPTY); setPicked(null); setErrors({}); setTried(false); setResult(null); };
    return html`<${Done} result=${result} form=${form} onAnother=${again} onClose=${onClose} />`;
  }

  const first = form.name.trim();
  const their = first ? `${first}'s` : 'their';
  const them = first || 'them';
  return html`<form class="signup" onSubmit=${submit} novalidate>
    <p class="body muted intro">
      Tell us about the person you look after. We'll watch how warm their home is and email you when it's getting too cold.
    </p>

    <h3 class="section-header">About them</h3>
    <section class="card form-card">
      <${Field} id="name" label="Their first name" error=${errors.name}>
        <${TextInput} id="name" form=${form} set=${set} error=${errors.name} autocomplete="off" />
      <//>
      <${Field} id="relationship_word" label="What do you call them? (optional)" hint="For example Mum or Grandad. Used in your alerts.">
        <${TextInput} id="relationship_word" form=${form} set=${set} hint="1" autocomplete="off" />
      <//>
      <${Field} id="age" label="Their age" error=${errors.age}>
        <${TextInput} id="age" form=${form} set=${set} error=${errors.age} inputmode="numeric" class="text-input short" />
      <//>
      <${Field} id="area" label="Town or area" hint="For the weather forecast." error=${errors.area}>
        <${TextInput} id="area" form=${form} set=${set} error=${errors.area} hint="1" autocomplete="address-level2" />
      <//>
    </section>

    <h3 class="section-header">${their} home</h3>
    <section class="card form-card">
      <${Choices} name="property_type" legend="What kind of home is it?" options=${PROPERTY_TYPES}
        value=${form.property_type} onChange=${(v) => set('property_type', v)} error=${errors.property_type} />
      <${Choices} name="rating" legend="Its energy rating" value=${form.rating}
        onChange=${(v) => { set('rating', v); setPicked(null); }}
        options=${[
          ['unknown', "I don't know", 'We start with a typical home and learn from the thermometers.'],
          ['band', 'I know the band (A to G)', "It's on the home's energy certificate."],
          ['lookup', 'Look it up for me', 'From the energy certificate register, with their agreement.'],
        ]} />
      ${form.rating === 'band' && html`
        <${Field} id="epc_band" label="Band" error=${errors.epc_band}>
          <select class="text-input short" id="epc_band" value=${form.epc_band}
            aria-invalid=${errors.epc_band ? 'true' : undefined}
            onChange=${(e) => set('epc_band', e.currentTarget.value)}>
            <option value="">Choose…</option>
            ${BANDS.map((b) => html`<option value=${b}>${b}</option>`)}
          </select>
        <//>`}
      ${form.rating === 'lookup' && html`<${Lookup} form=${form} set=${set} picked=${picked} setPicked=${pick} error=${errors.lookup} />`}
    </section>

    <h3 class="section-header">Emails</h3>
    <section class="card form-card">
      <${Field} id="contact_name" label="Your name" error=${errors.contact_name}>
        <${TextInput} id="contact_name" form=${form} set=${set} error=${errors.contact_name} autocomplete="name" />
      <//>
      <${Field} id="contact_email" label="Your email" hint="Where we send alerts about their home." error=${errors.contact_email}>
        <${TextInput} id="contact_email" form=${form} set=${set} error=${errors.contact_email} hint="1"
          type="email" autocomplete="email" inputmode="email" />
      <//>
      <${Field} id="person_email" label=${`${first ? `${first}'s` : 'Their'} email (optional)`}
        hint=${`We'll check in with ${them} if the home gets cold. Leave empty if ${first ? `${first} doesn't` : "they don't"} use email.`}
        error=${errors.person_email}>
        <${TextInput} id="person_email" form=${form} set=${set} error=${errors.person_email} hint="1"
          type="email" autocomplete="off" inputmode="email" />
      <//>
      <label class="choice">
        <input type="checkbox" checked=${form.night_alerts_opted_in}
          onChange=${(e) => set('night_alerts_opted_in', e.currentTarget.checked)} />
        <span class="choice-text">
          <span>Email me at night too</span>
          <span class="footnote muted">If the home gets dangerously cold overnight. We never contact ${them} at night.</span>
        </span>
      </label>
    </section>

    ${sendError && html`<p class="card footnote field-error" role="alert">Couldn't sign up: ${sendError}</p>`}
    ${tried && Object.keys(errors).length > 0 &&
    html`<p class="footnote field-error form-summary" role="alert">Check the ${Object.keys(errors).length === 1 ? 'answer' : 'answers'} marked in red.</p>`}

    <div class="form-actions">
      <button type="submit" class="button" disabled=${sending}>${sending ? 'Signing up…' : `Sign up`}</button>
      <button type="button" class="button secondary" onClick=${onClose}>Cancel</button>
    </div>
  </form>`;
}
