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


# ---------- more verbs, for the collections (same format, written compactly)
# Reflexive verbs: "me_levanto" keeps "me levanto" one form (the _ becomes a space).
GUSTAR_SUBJECT = VERBS["gustar"]["subject"]


def _verb(verb, meaning, rest, present, preterite, imperfect, en, inf, cmd=None, subject=None):
    """en: (present, preterite, imperfect) English yo sentences; inf: (es, en); cmd: (forms, rest, en)."""
    VERBS[verb] = {"meaning": meaning, "rest": rest,
                   "present": (present, f"Present tense: {en[0]}"),
                   "preterite": (preterite, f"Preterite: {en[1]}"),
                   "imperfect": (imperfect, f"Imperfect: {en[2]}")}
    if subject:
        VERBS[verb]["subject"] = subject
    INFINITIVES[verb] = inf
    if cmd:
        IMPERATIVES[verb] = (cmd[0], cmd[1], f"Command: {cmd[2]}")


# Daily routine
_verb("despertarse", "to wake up", "temprano.",
      "me_despierto te_despiertas se_despierta nos_despertamos os_despertáis se_despiertan",
      "me_desperté te_despertaste se_despertó nos_despertamos os_despertasteis se_despertaron",
      "me_despertaba te_despertabas se_despertaba nos_despertábamos os_despertabais se_despertaban",
      ("I wake up early.", "I woke up early.", "I used to wake up early."),
      ("Quiero despertarme temprano.", "I want to wake up early."),
      ("despiértate despiértese despertémonos despiértense", "temprano.", "Wake up early."))
_verb("levantarse", "to get up", "a las siete.",
      "me_levanto te_levantas se_levanta nos_levantamos os_levantáis se_levantan",
      "me_levanté te_levantaste se_levantó nos_levantamos os_levantasteis se_levantaron",
      "me_levantaba te_levantabas se_levantaba nos_levantábamos os_levantabais se_levantaban",
      ("I get up at seven.", "I got up at seven.", "I used to get up at seven."),
      ("Tengo que levantarme a las siete.", "I have to get up at seven."),
      ("levántate levántese levantémonos levántense", "a las siete.", "Get up at seven."))
_verb("ducharse", "to shower", "por la mañana.",
      "me_ducho te_duchas se_ducha nos_duchamos os_ducháis se_duchan",
      "me_duché te_duchaste se_duchó nos_duchamos os_duchasteis se_ducharon",
      "me_duchaba te_duchabas se_duchaba nos_duchábamos os_duchabais se_duchaban",
      ("I shower in the morning.", "I showered in the morning.", "I used to shower in the morning."),
      ("Necesito ducharme por la mañana.", "I need to shower in the morning."),
      ("dúchate dúchese duchémonos dúchense", "ahora.", "Shower now."))
_verb("vestirse", "to get dressed", "rápido.",
      "me_visto te_vistes se_viste nos_vestimos os_vestís se_visten",
      "me_vestí te_vestiste se_vistió nos_vestimos os_vestisteis se_vistieron",
      "me_vestía te_vestías se_vestía nos_vestíamos os_vestíais se_vestían",
      ("I get dressed quickly.", "I got dressed quickly.", "I used to get dressed quickly."),
      ("Tengo que vestirme rápido.", "I have to get dressed quickly."),
      ("vístete vístase vistámonos vístanse", "rápido.", "Get dressed quickly."))
_verb("desayunar", "to have breakfast", "huevos.",
      "desayuno desayunas desayuna desayunamos desayunáis desayunan",
      "desayuné desayunaste desayunó desayunamos desayunasteis desayunaron",
      "desayunaba desayunabas desayunaba desayunábamos desayunabais desayunaban",
      ("I have eggs for breakfast.", "I had eggs for breakfast.", "I used to have eggs for breakfast."),
      ("Quiero desayunar huevos.", "I want to have eggs for breakfast."),
      ("desayuna desayune desayunemos desayunen", "algo.", "Have some breakfast."))
_verb("volver", "to come back, to return", "a casa.",
      "vuelvo vuelves vuelve volvemos volvéis vuelven",
      "volví volviste volvió volvimos volvisteis volvieron",
      "volvía volvías volvía volvíamos volvíais volvían",
      ("I come back home.", "I came back home.", "I used to come back home."),
      ("Quiero volver a casa.", "I want to come back home."),
      ("vuelve vuelva volvamos vuelvan", "a casa.", "Come back home."))
_verb("acostarse", "to go to bed", "tarde.",
      "me_acuesto te_acuestas se_acuesta nos_acostamos os_acostáis se_acuestan",
      "me_acosté te_acostaste se_acostó nos_acostamos os_acostasteis se_acostaron",
      "me_acostaba te_acostabas se_acostaba nos_acostábamos os_acostabais se_acostaban",
      ("I go to bed late.", "I went to bed late.", "I used to go to bed late."),
      ("No quiero acostarme tarde.", "I don't want to go to bed late."),
      ("acuéstate acuéstese acostémonos acuéstense", "temprano.", "Go to bed early."))

# Food & eating out
_verb("beber", "to drink", "agua.",
      "bebo bebes bebe bebemos bebéis beben",
      "bebí bebiste bebió bebimos bebisteis bebieron",
      "bebía bebías bebía bebíamos bebíais bebían",
      ("I drink water.", "I drank water.", "I used to drink water."),
      ("Necesito beber agua.", "I need to drink water."),
      ("bebe beba bebamos beban", "agua.", "Drink water."))
_verb("cocinar", "to cook", "la cena.",
      "cocino cocinas cocina cocinamos cocináis cocinan",
      "cociné cocinaste cocinó cocinamos cocinasteis cocinaron",
      "cocinaba cocinabas cocinaba cocinábamos cocinabais cocinaban",
      ("I cook dinner.", "I cooked dinner.", "I used to cook dinner."),
      ("Quiero cocinar la cena.", "I want to cook dinner."),
      ("cocina cocine cocinemos cocinen", "la cena.", "Cook dinner."))
_verb("pedir", "to ask for, to order", "un café.",
      "pido pides pide pedimos pedís piden",
      "pedí pediste pidió pedimos pedisteis pidieron",
      "pedía pedías pedía pedíamos pedíais pedían",
      ("I order a coffee.", "I ordered a coffee.", "I used to order a coffee."),
      ("Voy a pedir un café.", "I'm going to order a coffee."),
      ("pide pida pidamos pidan", "un café.", "Order a coffee."))
_verb("pagar", "to pay", "la cuenta.",
      "pago pagas paga pagamos pagáis pagan",
      "pagué pagaste pagó pagamos pagasteis pagaron",
      "pagaba pagabas pagaba pagábamos pagabais pagaban",
      ("I pay the bill.", "I paid the bill.", "I used to pay the bill."),
      ("Quiero pagar la cuenta.", "I want to pay the bill."),
      ("paga pague paguemos paguen", "la cuenta.", "Pay the bill."))
_verb("tomar", "to take, to have (a drink)", "un té.",
      "tomo tomas toma tomamos tomáis toman",
      "tomé tomaste tomó tomamos tomasteis tomaron",
      "tomaba tomabas tomaba tomábamos tomabais tomaban",
      ("I have a tea.", "I had a tea.", "I used to have a tea."),
      ("Quiero tomar un té.", "I want to have a tea."),
      ("toma tome tomemos tomen", "un té.", "Have a tea."))
_verb("cenar", "to have dinner", "en un restaurante.",
      "ceno cenas cena cenamos cenáis cenan",
      "cené cenaste cenó cenamos cenasteis cenaron",
      "cenaba cenabas cenaba cenábamos cenabais cenaban",
      ("I have dinner at a restaurant.", "I had dinner at a restaurant.", "I used to have dinner at a restaurant."),
      ("Quiero cenar en un restaurante.", "I want to have dinner at a restaurant."),
      ("cena cene cenemos cenen", "en casa.", "Have dinner at home."))

# Getting around
_verb("llegar", "to arrive", "tarde.",
      "llego llegas llega llegamos llegáis llegan",
      "llegué llegaste llegó llegamos llegasteis llegaron",
      "llegaba llegabas llegaba llegábamos llegabais llegaban",
      ("I arrive late.", "I arrived late.", "I used to arrive late."),
      ("No quiero llegar tarde.", "I don't want to arrive late."),
      ("llega llegue lleguemos lleguen", "temprano.", "Arrive early."))
_verb("salir", "to leave, to go out", "de casa.",
      "salgo sales sale salimos salís salen",
      "salí saliste salió salimos salisteis salieron",
      "salía salías salía salíamos salíais salían",
      ("I leave the house.", "I left the house.", "I used to leave the house."),
      ("Tengo que salir de casa.", "I have to leave the house."),
      ("sal salga salgamos salgan", "de casa.", "Leave the house."))
_verb("viajar", "to travel", "a México.",
      "viajo viajas viaja viajamos viajáis viajan",
      "viajé viajaste viajó viajamos viajasteis viajaron",
      "viajaba viajabas viajaba viajábamos viajabais viajaban",
      ("I travel to Mexico.", "I traveled to Mexico.", "I used to travel to Mexico."),
      ("Quiero viajar a México.", "I want to travel to Mexico."),
      ("viaja viaje viajemos viajen", "a México.", "Travel to Mexico."))
_verb("caminar", "to walk", "al parque.",
      "camino caminas camina caminamos camináis caminan",
      "caminé caminaste caminó caminamos caminasteis caminaron",
      "caminaba caminabas caminaba caminábamos caminabais caminaban",
      ("I walk to the park.", "I walked to the park.", "I used to walk to the park."),
      ("Quiero caminar al parque.", "I want to walk to the park."),
      ("camina camine caminemos caminen", "al parque.", "Walk to the park."))

# Talking & listening
_verb("preguntar", "to ask", "la hora.",
      "pregunto preguntas pregunta preguntamos preguntáis preguntan",
      "pregunté preguntaste preguntó preguntamos preguntasteis preguntaron",
      "preguntaba preguntabas preguntaba preguntábamos preguntabais preguntaban",
      ("I ask the time.", "I asked the time.", "I used to ask the time."),
      ("Voy a preguntar la hora.", "I'm going to ask the time."),
      ("pregunta pregunte preguntemos pregunten", "la hora.", "Ask the time."))
_verb("contestar", "to answer", "el teléfono.",
      "contesto contestas contesta contestamos contestáis contestan",
      "contesté contestaste contestó contestamos contestasteis contestaron",
      "contestaba contestabas contestaba contestábamos contestabais contestaban",
      ("I answer the phone.", "I answered the phone.", "I used to answer the phone."),
      ("Tengo que contestar el teléfono.", "I have to answer the phone."),
      ("contesta conteste contestemos contesten", "el teléfono.", "Answer the phone."))
_verb("llamar", "to call", "a mi madre.",
      "llamo llamas llama llamamos llamáis llaman",
      "llamé llamaste llamó llamamos llamasteis llamaron",
      "llamaba llamabas llamaba llamábamos llamabais llamaban",
      ("I call my mother.", "I called my mother.", "I used to call my mother."),
      ("Voy a llamar a mi madre.", "I'm going to call my mother."),
      ("llama llame llamemos llamen", "a mi madre.", "Call my mother."))
_verb("escribir", "to write", "una carta.",
      "escribo escribes escribe escribimos escribís escriben",
      "escribí escribiste escribió escribimos escribisteis escribieron",
      "escribía escribías escribía escribíamos escribíais escribían",
      ("I write a letter.", "I wrote a letter.", "I used to write a letter."),
      ("Quiero escribir una carta.", "I want to write a letter."),
      ("escribe escriba escribamos escriban", "una carta.", "Write a letter."))
_verb("leer", "to read", "un libro.",
      "leo lees lee leemos leéis leen",
      "leí leíste leyó leímos leísteis leyeron",
      "leía leías leía leíamos leíais leían",
      ("I read a book.", "I read a book yesterday.", "I used to read a book."),
      ("Quiero leer un libro.", "I want to read a book."),
      ("lee lea leamos lean", "un libro.", "Read a book."))
_verb("escuchar", "to listen to", "música.",
      "escucho escuchas escucha escuchamos escucháis escuchan",
      "escuché escuchaste escuchó escuchamos escuchasteis escucharon",
      "escuchaba escuchabas escuchaba escuchábamos escuchabais escuchaban",
      ("I listen to music.", "I listened to music.", "I used to listen to music."),
      ("Quiero escuchar música.", "I want to listen to music."),
      ("escucha escuche escuchemos escuchen", "la canción.", "Listen to the song."))

# Wants, needs & likes
_verb("preferir", "to prefer", "el té.",
      "prefiero prefieres prefiere preferimos preferís prefieren",
      "preferí preferiste prefirió preferimos preferisteis prefirieron",
      "prefería preferías prefería preferíamos preferíais preferían",
      ("I prefer tea.", "I preferred tea.", "I used to prefer tea."),
      ("Es normal preferir el té.", "It's normal to prefer tea."))
_verb("esperar", "to wait (for), to hope", "el autobús.",
      "espero esperas espera esperamos esperáis esperan",
      "esperé esperaste esperó esperamos esperasteis esperaron",
      "esperaba esperabas esperaba esperábamos esperabais esperaban",
      ("I wait for the bus.", "I waited for the bus.", "I used to wait for the bus."),
      ("Tengo que esperar el autobús.", "I have to wait for the bus."),
      ("espera espere esperemos esperen", "aquí.", "Wait here."))
_verb("encantar", "to love (a thing)", "la música.",
      "encanta encanta encanta encanta encanta encanta",
      "encantó encantó encantó encantó encantó encantó",
      "encantaba encantaba encantaba encantaba encantaba encantaba",
      ("I love music.", "I loved the music.", "I used to love music."),
      ("Te va a encantar la música.", "You're going to love the music."),
      subject=GUSTAR_SUBJECT)
_verb("deber", "should, to owe", "estudiar más.",
      "debo debes debe debemos debéis deben",
      "debí debiste debió debimos debisteis debieron",
      "debía debías debía debíamos debíais debían",
      ("I should study more.", "I should have studied more.", "I was supposed to study more."),
      ("No quiero deber dinero.", "I don't want to owe money."))

# Thinking & knowing
_verb("creer", "to believe", "la historia.",
      "creo crees cree creemos creéis creen",
      "creí creíste creyó creímos creísteis creyeron",
      "creía creías creía creíamos creíais creían",
      ("I believe the story.", "I believed the story.", "I used to believe the story."),
      ("Es difícil creer la historia.", "It's hard to believe the story."))
_verb("entender", "to understand", "la pregunta.",
      "entiendo entiendes entiende entendemos entendéis entienden",
      "entendí entendiste entendió entendimos entendisteis entendieron",
      "entendía entendías entendía entendíamos entendíais entendían",
      ("I understand the question.", "I understood the question.", "I used to understand the question."),
      ("Quiero entender la pregunta.", "I want to understand the question."))
_verb("recordar", "to remember", "su nombre.",
      "recuerdo recuerdas recuerda recordamos recordáis recuerdan",
      "recordé recordaste recordó recordamos recordasteis recordaron",
      "recordaba recordabas recordaba recordábamos recordabais recordaban",
      ("I remember her name.", "I remembered her name.", "I used to remember her name."),
      ("Necesito recordar su nombre.", "I need to remember her name."),
      ("recuerda recuerde recordemos recuerden", "su nombre.", "Remember her name."))
_verb("aprender", "to learn", "español.",
      "aprendo aprendes aprende aprendemos aprendéis aprenden",
      "aprendí aprendiste aprendió aprendimos aprendisteis aprendieron",
      "aprendía aprendías aprendía aprendíamos aprendíais aprendían",
      ("I learn Spanish.", "I learned Spanish.", "I used to learn Spanish."),
      ("Quiero aprender español.", "I want to learn Spanish."),
      ("aprende aprenda aprendamos aprendan", "español.", "Learn Spanish."))
_verb("olvidar", "to forget", "las llaves.",
      "olvido olvidas olvida olvidamos olvidáis olvidan",
      "olvidé olvidaste olvidó olvidamos olvidasteis olvidaron",
      "olvidaba olvidabas olvidaba olvidábamos olvidabais olvidaban",
      ("I forget the keys.", "I forgot the keys.", "I used to forget the keys."),
      ("No quiero olvidar las llaves.", "I don't want to forget the keys."))

# Home, work & life
_verb("trabajar", "to work", "en una oficina.",
      "trabajo trabajas trabaja trabajamos trabajáis trabajan",
      "trabajé trabajaste trabajó trabajamos trabajasteis trabajaron",
      "trabajaba trabajabas trabajaba trabajábamos trabajabais trabajaban",
      ("I work in an office.", "I worked in an office.", "I used to work in an office."),
      ("Quiero trabajar en una oficina.", "I want to work in an office."),
      ("trabaja trabaje trabajemos trabajen", "mucho.", "Work hard."))
_verb("estudiar", "to study", "por la noche.",
      "estudio estudias estudia estudiamos estudiáis estudian",
      "estudié estudiaste estudió estudiamos estudiasteis estudiaron",
      "estudiaba estudiabas estudiaba estudiábamos estudiabais estudiaban",
      ("I study at night.", "I studied at night.", "I used to study at night."),
      ("Tengo que estudiar por la noche.", "I have to study at night."),
      ("estudia estudie estudiemos estudien", "para el examen.", "Study for the exam."))
_verb("dormir", "to sleep", "ocho horas.",
      "duermo duermes duerme dormimos dormís duermen",
      "dormí dormiste durmió dormimos dormisteis durmieron",
      "dormía dormías dormía dormíamos dormíais dormían",
      ("I sleep eight hours.", "I slept eight hours.", "I used to sleep eight hours."),
      ("Necesito dormir ocho horas.", "I need to sleep eight hours."),
      ("duerme duerma durmamos duerman", "ocho horas.", "Sleep eight hours."))
_verb("limpiar", "to clean", "la casa.",
      "limpio limpias limpia limpiamos limpiáis limpian",
      "limpié limpiaste limpió limpiamos limpiasteis limpiaron",
      "limpiaba limpiabas limpiaba limpiábamos limpiabais limpiaban",
      ("I clean the house.", "I cleaned the house.", "I used to clean the house."),
      ("Tengo que limpiar la casa.", "I have to clean the house."),
      ("limpia limpie limpiemos limpien", "la casa.", "Clean the house."))
_verb("comprar", "to buy", "comida.",
      "compro compras compra compramos compráis compran",
      "compré compraste compró compramos comprasteis compraron",
      "compraba comprabas compraba comprábamos comprabais compraban",
      ("I buy food.", "I bought food.", "I used to buy food."),
      ("Voy a comprar comida.", "I'm going to buy food."),
      ("compra compre compremos compren", "comida.", "Buy food."))
_verb("ayudar", "to help", "a mi amigo.",
      "ayudo ayudas ayuda ayudamos ayudáis ayudan",
      "ayudé ayudaste ayudó ayudamos ayudasteis ayudaron",
      "ayudaba ayudabas ayudaba ayudábamos ayudabais ayudaban",
      ("I help my friend.", "I helped my friend.", "I used to help my friend."),
      ("Quiero ayudar a mi amigo.", "I want to help my friend."),
      ("ayuda ayude ayudemos ayuden", "a mi amigo.", "Help my friend."))

# Collections shown in the app: id, title, description, verbs (a verb can be in several)
COLLECTIONS = [
    ("essentials", "The essentials", "The verbs everything else leans on",
     ["ser", "estar", "tener", "hacer", "ir", "haber", "poder", "dar"]),
    ("routine", "Daily routine", "Morning to night",
     ["despertarse", "levantarse", "ducharse", "vestirse", "desayunar", "volver", "acostarse"]),
    ("food", "Food & eating out", "Ordering, cooking, paying",
     ["comer", "beber", "cocinar", "pedir", "pagar", "tomar", "cenar"]),
    ("around", "Getting around", "Coming, going and traveling",
     ["ir", "venir", "llegar", "salir", "viajar", "caminar", "volver"]),
    ("talking", "Talking & listening", "Conversations, calls and reading",
     ["hablar", "decir", "preguntar", "contestar", "llamar", "escribir", "leer", "escuchar"]),
    ("wants", "Wants, needs & likes", "Saying what you want and like",
     ["querer", "necesitar", "gustar", "preferir", "esperar", "encantar", "deber"]),
    ("thinking", "Thinking & knowing", "Knowing, remembering, learning",
     ["saber", "conocer", "pensar", "creer", "entender", "recordar", "aprender", "olvidar"]),
    ("life", "Home, work & life", "Everyday life at home and work",
     ["vivir", "ver", "trabajar", "estudiar", "dormir", "limpiar", "comprar", "ayudar"]),
]


# ---------- for reading: each sentence's English, and which words are the
# pronoun and the verb (the app colors them). Written from the yo English:
# "I get up at seven." -> "She gets up at seven."
EN_SUBJECT = {"yo": "I", "tu": "You", "usted": "You", "el": "He", "ella": "She", "nosotros": "We",
              "nosotras": "We", "ustedes": "You all", "ellos": "They", "ellas": "They"}
EN_PLURAL = {"ser": ("a student", "students")}  # Spanish rest that goes plural: its English too
CLITICS = {"me", "te", "se", "nos", "os", "le", "les", "lo", "la", "los", "las"}


def _third_person(word):
    if word in ("can", "should", "must", "will", "could", "would"):
        return word
    irregular = {"am": "is", "have": "has", "do": "does", "go": "goes"}
    if word in irregular:
        return irregular[word]
    if word.endswith(("ch", "sh", "s", "x", "z", "o")):
        return word + "es"
    if word.endswith("y") and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


def english_for(verb, tense, en_yo, pid, slot):
    """The yo sentence's English said by another pronoun."""
    words = en_yo.split(" ")
    assert words[0] == "I", (verb, en_yo)
    if pid == "yo":
        return en_yo
    first = words[1]
    if tense == "present":
        first = _third_person(first) if pid in ("el", "ella") else ("are" if first == "am" else first)
    elif first == "was" and pid not in ("el", "ella"):
        first = "were"
    text = " ".join([EN_SUBJECT[pid], first] + words[2:])
    if verb in EN_PLURAL and slot >= 3:
        text = text.replace(*EN_PLURAL[verb])
    return text


def parts_for(subject, form, rest):
    """[[text, kind]]: kind "p" pronoun, "v" verb, "" the rest. "me levanto" -> me (p) levanto (v)."""
    words = form.split(" ")
    lead = []
    while len(words) > 1 and words[0] in CLITICS:
        lead.append(words.pop(0))
    subject_words = subject.split(" ")
    out = []
    if subject_words[0] == "A":  # gustar: "A ella le" -> A, then the pronouns
        out.append(["A", ""])
        subject_words = subject_words[1:]
    out.append([" ".join(subject_words + lead), "p"])
    out.append([" ".join(words), "v"])
    out.append([rest, ""])
    return out


def infinitive_parts(verb, es):
    """The infinitive (with any pronoun on the end, "levantarme") is the verb."""
    stem = verb[:-2] if verb.endswith("se") else verb
    words = es.split(" ")
    i = next((k for k, w in enumerate(words) if w.lower().startswith(stem)), None)
    if i is None:
        return [[es, ""]]
    return [x for x in ([" ".join(words[:i]), ""], [words[i], "v"], [" ".join(words[i + 1:]), ""]) if x[0]]



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
                                  "single": {"es": es, "clip": spk.clip(es, "es"), "parts": infinitive_parts(verb, es)}})
            continue
        if tid == "imperative":
            if verb in IMPERATIVES:
                out["tenses"].append(imperative(verb, label, spk))
            continue
        if tid not in v:
            continue
        forms, en = v[tid]
        forms = [f.replace("_", " ") for f in forms.split()]  # me_levanto -> me levanto
        assert len(forms) == 6, (verb, tid)
        t = {"id": tid, "label": label, "en": en, "en_clip": spk.clip(en, "en"), "forms": {}}
        for pid, es, _, slot in PRONOUNS:
            subject = v.get("subject", {}).get(pid, es)
            text = f"{subject} {forms[slot]} {rest[slot >= 3]}"
            t["forms"][pid] = {"es": text, "clip": spk.clip(text, "es"),
                               "en": english_for(verb, tid, en.split(": ", 1)[1], pid, slot),
                               "parts": parts_for(subject, forms[slot], rest[slot >= 3])}
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
        en_cmd = en.split(": ", 1)[1]
        t["forms"][pid] = {"es": text, "clip": spk.clip(text, "es"),
                           "en": f"Let's {en_cmd[0].lower()}{en_cmd[1:]}" if slot == 2 else en_cmd,
                           "parts": [[text.split(" ", 1)[0], "v"], [text.split(" ", 1)[1], ""]]}
    return t


def write_index():
    built = {f.stem for f in OUT.glob("*.json") if f.stem in VERBS}
    (OUT / "index.json").write_text(json.dumps({
        "verbs": [{"verb": k, "meaning": VERBS[k]["meaning"]} for k in VERBS if k in built],
        "collections": [{"id": cid, "title": title, "desc": desc, "verbs": [v for v in verbs if v in built]}
                        for cid, title, desc, verbs in COLLECTIONS],
    }, ensure_ascii=False, indent=2))
    # Every verb in one file, so a session with many verbs is one request, not one each
    (OUT / "all.json").write_text(json.dumps(
        {"verbs": [json.loads((OUT / f"{k}.json").read_text()) for k in VERBS if k in built]},
        ensure_ascii=False, separators=(",", ":")))


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
