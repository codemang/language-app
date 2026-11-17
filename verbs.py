#!/usr/bin/env python3
"""Verb drills: short audio clips the app strings together on the phone.

For each verb there's one simple sentence, conjugated for every pronoun in three
tenses (present, preterite, imperfect). Each Spanish sentence, each tense's English
intro ("Present tense: I have a dog.") and each pronoun cue ("She form.") is its own
clip, so the app can play any mix of pronouns and tenses the user ticks.

Free: Kokoro runs on the Pi (English voice af_heart, Spanish voice ef_dora).
Clips are named by a hash of voice + text and shared between verbs, and existing
clips are reused, so re-running only generates what's new.

Output: web/verbs/clips/*.mp3, web/verbs/<verb>.json, web/verbs/index.json

  python verbs.py            # all verbs in VERBS
  python verbs.py tener
"""

import argparse
import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

from local_engine import KOKORO_MODEL, KOKORO_VOICES

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "web" / "verbs"
CLIPS = OUT / "clips"
VOICES = {"en": ("af_heart", "en-us"), "es": ("ef_dora", "es")}
SPEED = 0.9

# id, Spanish pronoun, English cue said before it, which conjugation slot it uses.
# Slots: 0 yo, 1 tú, 2 él/ella/usted, 3 nosotros, 4 vosotros, 5 ellos/ellas/ustedes.
# Latin American set: no vosotros.
PRONOUNS = [
    ("yo", "Yo", "I form.", 0),
    ("tu", "Tú", "You form.", 1),
    ("usted", "Usted", "You, formal form.", 2),
    ("el", "Él", "He form.", 2),
    ("ella", "Ella", "She form.", 2),
    ("nosotros", "Nosotros", "We form.", 3),
    ("nosotras", "Nosotras", "We, feminine form.", 3),
    ("ustedes", "Ustedes", "You all form.", 5),
    ("ellos", "Ellos", "They form.", 5),
    ("ellas", "Ellas", "They, feminine form.", 5),
]
TENSES = [("infinitive", "Infinitive"), ("present", "Present"), ("preterite", "Preterite"), ("imperfect", "Imperfect"), ("imperative", "Command")]

# verb -> example sentence with the infinitive, English. Mostly "I want to ...";
# a few verbs read better with another helper.
INFINITIVES = {
    "ser": ("Quiero ser estudiante.", "I want to be a student."),
    "estar": ("Quiero estar en casa.", "I want to be at home."),
    "tener": ("Quiero tener un perro.", "I want to have a dog."),
    "hacer": ("Tengo que hacer la tarea.", "I have to do the homework."),
    "ir": ("Quiero ir a la playa.", "I want to go to the beach."),
    "poder": ("Quiero poder nadar.", "I want to be able to swim."),
    "decir": ("Necesito decir la verdad.", "I need to tell the truth."),
    "querer": ("Es normal querer un café.", "It's normal to want a coffee."),
    "haber": ("Después de haber comido, duermo.", "After having eaten, I sleep."),
    "saber": ("Quiero saber la respuesta.", "I want to know the answer."),
    "ver": ("Quiero ver la televisión.", "I want to watch TV."),
    "dar": ("Quiero dar un regalo.", "I want to give a gift."),
    "venir": ("Puedo venir a la fiesta.", "I can come to the party."),
    "hablar": ("Quiero hablar español.", "I want to speak Spanish."),
    "comer": ("Quiero comer pan.", "I want to eat bread."),
    "vivir": ("Quiero vivir en la ciudad.", "I want to live in the city."),
    "gustar": ("Te va a gustar el café.", "You're going to like the coffee."),
    "necesitar": ("Voy a necesitar ayuda.", "I'm going to need help."),
    "conocer": ("Quiero conocer a María.", "I want to meet María."),
    "pensar": ("Necesito pensar en el viaje.", "I need to think about the trip."),
}

# Commands (affirmative imperative) only exist for "you" and "let's": tú, usted,
# nosotros/nosotras ("let's ..."), ustedes. Said without the pronoun, as people do:
# the English cue before each one says who it's for.
IMPERATIVE_PRONOUNS = {"tu": 0, "usted": 1, "nosotros": 2, "nosotras": 2, "ustedes": 3}

# verb -> (tú usted nosotros ustedes forms, rest of the sentence, English intro).
# rest is a (singular, plural) pair when it agrees in number. Verbs whose command
# form isn't used in everyday speech have none (haber, gustar, poder, querer,
# saber, necesitar), so the Command option doesn't show for them.
IMPERATIVES = {
    "ser": ("sé sea seamos sean", ("paciente.", "pacientes."), "Command: Be patient."),
    "estar": ("está esté estemos estén", "en casa a las ocho.", "Command: Be at home at eight."),
    "tener": ("ten tenga tengamos tengan", "paciencia.", "Command: Have patience."),
    "hacer": ("haz haga hagamos hagan", "la tarea.", "Command: Do the homework."),
    "ir": ("ve vaya vamos vayan", "a la playa.", "Command: Go to the beach."),
    "decir": ("di diga digamos digan", "la verdad.", "Command: Tell the truth."),
    "ver": ("ve vea veamos vean", "la televisión.", "Command: Watch TV."),
    "dar": ("da dé demos den", "un regalo.", "Command: Give a gift."),
    "venir": ("ven venga vengamos vengan", "a la fiesta.", "Command: Come to the party."),
    "hablar": ("habla hable hablemos hablen", "español.", "Command: Speak Spanish."),
    "comer": ("come coma comamos coman", "pan.", "Command: Eat bread."),
    "vivir": ("vive viva vivamos vivan", "en la ciudad.", "Command: Live in the city."),
    "conocer": ("conoce conozca conozcamos conozcan", "a María.", "Command: Meet María."),
    "pensar": ("piensa piense pensemos piensen", "en el viaje.", "Command: Think about the trip."),
}

# Each verb: one sentence, conjugated in up to three tenses.
#   rest     the sentence after the verb; a (singular, plural) pair when it agrees
#            in number with the subject (soy estudiante / somos estudiantes)
#   subject  optional, replaces the subject pronoun (gustar: "A mí me gusta")
#   <tense>  (6 forms: yo, tú, él, nosotros, vosotros, ellos), English intro (yo form)
# A verb without a tense skips it (haber has no everyday preterite: "hube" is literary).
VERBS = {
    "ser": {
        "meaning": "to be (who or what)",
        "rest": ("estudiante.", "estudiantes."),
        "present": ("soy eres es somos sois son", "Present tense: I am a student."),
        "preterite": ("fui fuiste fue fuimos fuisteis fueron", "Preterite: I was a student."),
        "imperfect": ("era eras era éramos erais eran", "Imperfect: I used to be a student."),
    },
    "estar": {
        "meaning": "to be (where or how)",
        "rest": "en casa.",
        "present": ("estoy estás está estamos estáis están", "Present tense: I am at home."),
        "preterite": ("estuve estuviste estuvo estuvimos estuvisteis estuvieron", "Preterite: I was at home."),
        "imperfect": ("estaba estabas estaba estábamos estabais estaban", "Imperfect: I used to be at home."),
    },
    "tener": {
        "meaning": "to have",
        "rest": "un perro.",
        "present": ("tengo tienes tiene tenemos tenéis tienen", "Present tense: I have a dog."),
        "preterite": ("tuve tuviste tuvo tuvimos tuvisteis tuvieron", "Preterite: I had a dog."),
        "imperfect": ("tenía tenías tenía teníamos teníais tenían", "Imperfect: I used to have a dog."),
    },
    "hacer": {
        "meaning": "to do, to make",
        "rest": "la tarea.",
        "present": ("hago haces hace hacemos hacéis hacen", "Present tense: I do the homework."),
        "preterite": ("hice hiciste hizo hicimos hicisteis hicieron", "Preterite: I did the homework."),
        "imperfect": ("hacía hacías hacía hacíamos hacíais hacían", "Imperfect: I used to do the homework."),
    },
    "ir": {
        "meaning": "to go",
        "rest": "a la playa.",
        "present": ("voy vas va vamos vais van", "Present tense: I go to the beach."),
        "preterite": ("fui fuiste fue fuimos fuisteis fueron", "Preterite: I went to the beach."),
        "imperfect": ("iba ibas iba íbamos ibais iban", "Imperfect: I used to go to the beach."),
    },
    "poder": {
        "meaning": "can, to be able to",
        "rest": "nadar.",
        "present": ("puedo puedes puede podemos podéis pueden", "Present tense: I can swim."),
        "preterite": ("pude pudiste pudo pudimos pudisteis pudieron", "Preterite: I managed to swim."),
        "imperfect": ("podía podías podía podíamos podíais podían", "Imperfect: I used to be able to swim."),
    },
    "decir": {
        "meaning": "to say, to tell",
        "rest": "la verdad.",
        "present": ("digo dices dice decimos decís dicen", "Present tense: I tell the truth."),
        "preterite": ("dije dijiste dijo dijimos dijisteis dijeron", "Preterite: I told the truth."),
        "imperfect": ("decía decías decía decíamos decíais decían", "Imperfect: I used to tell the truth."),
    },
    "querer": {
        "meaning": "to want",
        "rest": "un café.",
        "present": ("quiero quieres quiere queremos queréis quieren", "Present tense: I want a coffee."),
        "preterite": ("quise quisiste quiso quisimos quisisteis quisieron", "Preterite: I wanted a coffee."),
        "imperfect": ("quería querías quería queríamos queríais querían", "Imperfect: I used to want a coffee."),
    },
    "haber": {
        "meaning": "to have (done something)",
        "rest": "comido.",
        "present": ("he has ha hemos habéis han", "Present tense: I have eaten."),
        "imperfect": ("había habías había habíamos habíais habían", "Imperfect: I had eaten."),
    },
    "saber": {
        "meaning": "to know (facts)",
        "rest": "la respuesta.",
        "present": ("sé sabes sabe sabemos sabéis saben", "Present tense: I know the answer."),
        "preterite": ("supe supiste supo supimos supisteis supieron", "Preterite: I found out the answer."),
        "imperfect": ("sabía sabías sabía sabíamos sabíais sabían", "Imperfect: I knew the answer."),
    },
    "ver": {
        "meaning": "to see, to watch",
        "rest": "la televisión.",
        "present": ("veo ves ve vemos veis ven", "Present tense: I watch TV."),
        "preterite": ("vi viste vio vimos visteis vieron", "Preterite: I watched TV."),
        "imperfect": ("veía veías veía veíamos veíais veían", "Imperfect: I used to watch TV."),
    },
    "dar": {
        "meaning": "to give",
        "rest": "un regalo.",
        "present": ("doy das da damos dais dan", "Present tense: I give a gift."),
        "preterite": ("di diste dio dimos disteis dieron", "Preterite: I gave a gift."),
        "imperfect": ("daba dabas daba dábamos dabais daban", "Imperfect: I used to give a gift."),
    },
    "venir": {
        "meaning": "to come",
        "rest": "a la fiesta.",
        "present": ("vengo vienes viene venimos venís vienen", "Present tense: I come to the party."),
        "preterite": ("vine viniste vino vinimos vinisteis vinieron", "Preterite: I came to the party."),
        "imperfect": ("venía venías venía veníamos veníais venían", "Imperfect: I used to come to the party."),
    },
    "hablar": {
        "meaning": "to speak",
        "rest": "español.",
        "present": ("hablo hablas habla hablamos habláis hablan", "Present tense: I speak Spanish."),
        "preterite": ("hablé hablaste habló hablamos hablasteis hablaron", "Preterite: I spoke Spanish."),
        "imperfect": ("hablaba hablabas hablaba hablábamos hablabais hablaban", "Imperfect: I used to speak Spanish."),
    },
    "comer": {
        "meaning": "to eat",
        "rest": "pan.",
        "present": ("como comes come comemos coméis comen", "Present tense: I eat bread."),
        "preterite": ("comí comiste comió comimos comisteis comieron", "Preterite: I ate bread."),
        "imperfect": ("comía comías comía comíamos comíais comían", "Imperfect: I used to eat bread."),
    },
    "vivir": {
        "meaning": "to live",
        "rest": "en la ciudad.",
        "present": ("vivo vives vive vivimos vivís viven", "Present tense: I live in the city."),
        "preterite": ("viví viviste vivió vivimos vivisteis vivieron", "Preterite: I lived in the city."),
        "imperfect": ("vivía vivías vivía vivíamos vivíais vivían", "Imperfect: I used to live in the city."),
    },
    "gustar": {
        "meaning": "to like (to be pleasing)",
        "rest": "el café.",
        "subject": {"yo": "A mí me", "tu": "A ti te", "usted": "A usted le", "el": "A él le",
                    "ella": "A ella le", "nosotros": "A nosotros nos", "nosotras": "A nosotras nos",
                    "ustedes": "A ustedes les", "ellos": "A ellos les", "ellas": "A ellas les"},
        "present": ("gusta gusta gusta gusta gusta gusta", "Present tense: I like coffee."),
        "preterite": ("gustó gustó gustó gustó gustó gustó", "Preterite: I liked the coffee."),
        "imperfect": ("gustaba gustaba gustaba gustaba gustaba gustaba", "Imperfect: I used to like coffee."),
    },
    "necesitar": {
        "meaning": "to need",
        "rest": "ayuda.",
        "present": ("necesito necesitas necesita necesitamos necesitáis necesitan", "Present tense: I need help."),
        "preterite": ("necesité necesitaste necesitó necesitamos necesitasteis necesitaron", "Preterite: I needed help."),
        "imperfect": ("necesitaba necesitabas necesitaba necesitábamos necesitabais necesitaban", "Imperfect: I used to need help."),
    },
    "conocer": {
        "meaning": "to know (people, places)",
        "rest": "a María.",
        "present": ("conozco conoces conoce conocemos conocéis conocen", "Present tense: I know María."),
        "preterite": ("conocí conociste conoció conocimos conocisteis conocieron", "Preterite: I met María."),
        "imperfect": ("conocía conocías conocía conocíamos conocíais conocían", "Imperfect: I knew María."),
    },
    "pensar": {
        "meaning": "to think",
        "rest": "en el viaje.",
        "present": ("pienso piensas piensa pensamos pensáis piensan", "Present tense: I think about the trip."),
        "preterite": ("pensé pensaste pensó pensamos pensasteis pensaron", "Preterite: I thought about the trip."),
        "imperfect": ("pensaba pensabas pensaba pensábamos pensabais pensaban", "Imperfect: I used to think about the trip."),
    },
}


class Speaker:
    def __init__(self):
        self.kokoro = None
        self.made = 0

    def clip(self, text, lang):
        """Path (relative to web/verbs) of the clip for text, generating it if needed."""
        voice, code = VOICES[lang]
        name = hashlib.sha1(f"{voice}|{SPEED}|{text}".encode()).hexdigest()[:16] + ".mp3"
        path = CLIPS / name
        if not path.exists():
            from kokoro_onnx import Kokoro
            self.kokoro = self.kokoro or Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
            print(f"  {lang}: {text}", file=sys.stderr)
            samples, sr = self.kokoro.create(text, voice=voice, speed=SPEED, lang=code)
            CLIPS.mkdir(parents=True, exist_ok=True)
            wav = path.with_suffix(".tmp.wav")
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
            tmp = path.with_suffix(".tmp.mp3")
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                            "-c:a", "libmp3lame", "-b:a", "96k", str(tmp)], check=True)
            wav.unlink()
            tmp.replace(path)
            self.made += 1
        return f"clips/{name}"


def build(verb, spk):
    v = VERBS[verb]
    out = {
        "verb": verb,
        "meaning": v["meaning"],
        "pronouns": [{"id": pid, "es": es, "cue": cue, "cue_clip": spk.clip(cue, "en")}
                     for pid, es, cue, _ in PRONOUNS],
        "tenses": [],
    }
    rest = v["rest"] if isinstance(v["rest"], tuple) else (v["rest"], v["rest"])
    for tid, label in TENSES:
        if tid == "infinitive":
            # One example sentence using the infinitive (no pronouns)
            es, en = INFINITIVES[verb]
            en = f"Infinitive: {en}"
            out["tenses"].append({"id": tid, "label": label, "en": en, "en_clip": spk.clip(en, "en"),
                                  "single": {"es": es, "clip": spk.clip(es, "es")}})
            continue
        if tid == "imperative":
            if verb in IMPERATIVES:
                out["tenses"].append(imperative(verb, label, spk))
            continue
        if tid not in v:
            continue
        forms, en = v[tid]
        forms = forms.split()
        assert len(forms) == 6, (verb, tid)
        t = {"id": tid, "label": label, "en": en, "en_clip": spk.clip(en, "en"), "forms": {}}
        for pid, es, _, slot in PRONOUNS:
            subject = v.get("subject", {}).get(pid, es)
            text = f"{subject} {forms[slot]} {rest[slot >= 3]}"
            t["forms"][pid] = {"es": text, "clip": spk.clip(text, "es")}
        out["tenses"].append(t)
    (OUT / f"{verb}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))


def imperative(verb, label, spk):
    forms, rest, en = IMPERATIVES[verb]
    forms = forms.split()
    assert len(forms) == 4, verb
    rest = rest if isinstance(rest, tuple) else (rest, rest)
    t = {"id": "imperative", "label": label, "en": en, "en_clip": spk.clip(en, "en"), "forms": {}}
    for pid, slot in IMPERATIVE_PRONOUNS.items():
        text = f"{forms[slot][0].upper()}{forms[slot][1:]} {rest[slot >= 2]}"
        t["forms"][pid] = {"es": text, "clip": spk.clip(text, "es")}
    return t


def write_index():
    built = {f.stem for f in OUT.glob("*.json") if f.stem in VERBS}
    (OUT / "index.json").write_text(json.dumps(
        [{"verb": k, "meaning": VERBS[k]["meaning"]} for k in VERBS if k in built], ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("verbs", nargs="*", help=f"default: all ({', '.join(VERBS)})")
    args = p.parse_args()
    for v in args.verbs:
        if v not in VERBS:
            p.error(f"unknown verb {v!r}; add it to VERBS")
    spk = Speaker()
    for verb in args.verbs or VERBS:
        print(verb, file=sys.stderr)
        build(verb, spk)
        write_index()  # after each verb, so finished ones show in the app right away
    print(f"done, {spk.made} new clips", file=sys.stderr)


if __name__ == "__main__":
    main()
