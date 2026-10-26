import { getPrefs, subscribePrefs } from './prefs'

// Backstage's sound engine. Every sound is synthesized with Tone.js, so there
// are no audio files to load or license.
//
// Tone is a big library, so it loads after the page has painted. Browsers only
// let audio start from a tap or key press: each toy calls play() from its
// event handler, and the first call wakes the audio up.

let Tone = null
let voices = null
const loading = import('tone').then((mod) => {
  Tone = mod
})

// Monophonic synths throw when two notes start at the same instant, which a
// fast drag across bubbles can do. Each voice keeps its own last start time.
const lastStart = {}
const GAP = 0.012

function startTime(name, time) {
  const t = Math.max(time ?? Tone.now(), (lastStart[name] ?? 0) + GAP)
  lastStart[name] = t
  return t
}

function build() {
  // Everything goes through one gentle low-pass, which Calm turns down.
  const master = new Tone.Filter(18000, 'lowpass').toDestination()
  const out = (node, db = 0) => {
    node.volume.value = db
    return node.connect(master)
  }
  const steamFilter = new Tone.Filter(2600, 'highpass').connect(master)
  const pourGain = new Tone.Gain(0).connect(master)
  const pourFilter = new Tone.Filter({ frequency: 650, type: 'lowpass', Q: 1.2 }).connect(pourGain)
  return {
    master,
    pop: out(new Tone.MembraneSynth({
      pitchDecay: 0.008,
      octaves: 2.5,
      envelope: { attack: 0.001, decay: 0.09, sustain: 0, release: 0.02 },
    }), -6),
    clink: out(new Tone.MetalSynth({
      harmonicity: 12,
      modulationIndex: 20,
      resonance: 5000,
      octaves: 0.6,
      envelope: { attack: 0.001, decay: 0.18, release: 0.05 },
    }), -18),
    spoon: out(new Tone.PluckSynth({ attackNoise: 1.2, dampening: 4200, resonance: 0.86 }), -2),
    tung: out(new Tone.MembraneSynth({
      pitchDecay: 0.02,
      octaves: 2,
      envelope: { attack: 0.001, decay: 0.2, sustain: 0, release: 0.05 },
    }), -2),
    steam: (() => {
      const synth = new Tone.NoiseSynth({
        noise: { type: 'white' },
        envelope: { attack: 0.03, decay: 0.35, sustain: 0, release: 0.05 },
      }).connect(steamFilter)
      synth.volume.value = -16
      return synth
    })(),
    chime: out(new Tone.PolySynth(Tone.Synth, {
      oscillator: { type: 'sine' },
      envelope: { attack: 0.005, decay: 0.9, sustain: 0, release: 0.3 },
    }), -10),
    pour: { noise: new Tone.Noise('pink').connect(pourFilter), gain: pourGain },
  }
}

function applyPrefs() {
  if (!Tone || !voices) return
  const { sound, volume, calm } = getPrefs()
  const destination = Tone.getDestination()
  destination.mute = !sound || volume <= 0
  if (volume > 0) destination.volume.rampTo(20 * Math.log10(volume) - (calm ? 6 : 0), 0.05)
  voices.master.frequency.rampTo(calm ? 3200 : 18000, 0.1)
}

subscribePrefs(applyPrefs)

// Wake the audio up. Call it from a tap or key press; it is safe to call often.
export async function unlockAudio() {
  if (!Tone) await loading
  if (Tone.getContext().state !== 'running') await Tone.start()
  if (!voices) {
    voices = build()
    applyPrefs()
  }
  return Tone
}

// Runs `fn` now if the audio is awake, else right after waking it, so the
// very first tap still makes its sound.
function whenReady(fn) {
  if (Tone && voices && Tone.getContext().state === 'running') {
    try {
      fn()
    } catch {
      // A sound that can't be scheduled is skipped, never fatal to a toy.
    }
    return
  }
  unlockAudio().then(() => whenReady(fn), () => {})
}

const POP_NOTES = ['G5', 'A5', 'B5', 'C6', 'D6', 'E6']

// One-shot sounds. `time` is a Tone time for the looper; toys leave it out.
const SOUNDS = {
  pop: (time) => voices.pop.triggerAttackRelease(
    POP_NOTES[Math.floor(Math.random() * POP_NOTES.length)], '32n', startTime('pop', time), 0.7,
  ),
  clink: (time) => voices.clink.triggerAttackRelease('A5', '32n', startTime('clink', time), 0.8),
  spoon: (time) => voices.spoon.triggerAttack('E5', startTime('spoon', time)),
  tung: (time) => voices.tung.triggerAttackRelease('D3', '16n', startTime('tung', time), 0.9),
  steam: (time) => voices.steam.triggerAttackRelease(0.3, startTime('steam', time), 0.8),
}

export function play(name, time) {
  whenReady(() => SOUNDS[name](time))
}

// A soft music-box note; `step` walks up a pentatonic scale.
const CHIME_NOTES = ['C5', 'D5', 'E5', 'G5', 'A5', 'C6', 'D6', 'E6']

export function chime(step = 0) {
  const note = CHIME_NOTES[((step % CHIME_NOTES.length) + CHIME_NOTES.length) % CHIME_NOTES.length]
  whenReady(() => voices.chime.triggerAttackRelease(note, '8n', Tone.now(), 0.6))
}

// A quick rising run, for "you did it" moments.
export function flourish() {
  whenReady(() => {
    const start = Tone.now()
    ;['C5', 'E5', 'G5', 'C6'].forEach((note, i) => {
      voices.chime.triggerAttackRelease(note, '16n', start + i * 0.07, 0.5)
    })
  })
}

// The pouring-milk hiss in the latte toy, faded in and out.
export function setPouring(on) {
  if (!on && !voices) return
  whenReady(() => {
    const { noise, gain } = voices.pour
    if (on && noise.state !== 'started') noise.start()
    gain.gain.rampTo(on ? 0.5 : 0, on ? 0.08 : 0.25)
  })
}
