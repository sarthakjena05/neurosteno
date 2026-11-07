"""Curated, hierarchical medical phrase catalog.

Every level is a semantically meaningful question ("Where does it hurt?", "What would
help?"), and every child carries a semantic ``group`` label used when fewer SSVEP targets
are available than children (see navigation.py). There are no numbered/arbitrary groups.

Leaves carry the full output sentence, e.g.
    Home > Pain > Legs and feet > Left leg > Reposition me > Skip rating
    -> "My left leg hurts. Please help me reposition."

Node ids are stable slash paths, used by patient profiles and shortcuts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache


@dataclass
class Node:
    id: str
    label: str
    group: str = ""
    prompt: str = ""  # question shown when this node's children are on screen
    phrase: str | None = None  # output sentence (leaves only)
    icon: str = ""
    urgent: bool = False
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def add(self, child: "Node") -> "Node":
        child.parent = self
        self.children.append(child)
        return child

    def path(self) -> list["Node"]:
        out, n = [], self
        while n is not None:
            out.append(n)
            n = n.parent
        return out[::-1]

    @property
    def title(self) -> str:
        """Human-readable path without the root, e.g. 'Pain › Left leg › Reposition me'."""
        return " › ".join(n.label for n in self.path()[1:])


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-").replace("--", "-")


def child(parent: Node, slug: str, label: str, group: str = "", prompt: str = "", phrase: str | None = None,
          icon: str = "", urgent: bool = False) -> Node:
    return parent.add(Node(id=f"{parent.id}/{slug}" if parent.id != "home" else slug, label=label, group=group,
                           prompt=prompt, phrase=phrase, icon=icon, urgent=urgent))


# ---------------------------------------------------------------------------------------
# PAIN: region -> body part -> what would help -> how bad
# ---------------------------------------------------------------------------------------
# part tuple: (slug, label, subject noun phrase, plural?, group within region, excluded actions)
PAIN_REGIONS = [
    ("head-face", "Head and face", "Upper body", [
        ("head", "Head", "my head", False, "Head", {"massage"}),
        ("eyes", "Eyes", "my eyes", True, "Head", {"massage", "ice"}),
        ("ears", "Ears", "my ears", True, "Head", {"massage", "ice"}),
        ("jaw", "Jaw", "my jaw", False, "Mouth and throat", set()),
        ("mouth", "Mouth or teeth", "my mouth", False, "Mouth and throat", {"massage", "heat"}),
        ("throat", "Throat", "my throat", False, "Mouth and throat", {"massage", "heat"}),
    ]),
    ("neck-shoulders", "Neck and shoulders", "Upper body", [
        ("neck", "Neck", "my neck", False, "Neck", set()),
        ("left-shoulder", "Left shoulder", "my left shoulder", False, "Shoulders", set()),
        ("right-shoulder", "Right shoulder", "my right shoulder", False, "Shoulders", set()),
        ("both-shoulders", "Both shoulders", "both of my shoulders", True, "Shoulders", set()),
    ]),
    ("arms-hands", "Arms and hands", "Upper body", [
        ("left-arm", "Left arm", "my left arm", False, "Left side", set()),
        ("left-elbow", "Left elbow", "my left elbow", False, "Left side", set()),
        ("left-wrist", "Left wrist", "my left wrist", False, "Left side", set()),
        ("left-hand", "Left hand", "my left hand", False, "Left side", set()),
        ("right-arm", "Right arm", "my right arm", False, "Right side", set()),
        ("right-elbow", "Right elbow", "my right elbow", False, "Right side", set()),
        ("right-wrist", "Right wrist", "my right wrist", False, "Right side", set()),
        ("right-hand", "Right hand", "my right hand", False, "Right side", set()),
    ]),
    ("chest-back", "Chest and back", "Upper body", [
        ("chest", "Chest", "my chest", False, "Front", {"massage", "ice", "heat"}),
        ("ribs", "Ribs", "my ribs", True, "Front", {"massage"}),
        ("upper-back", "Upper back", "my upper back", False, "Back", set()),
        ("lower-back", "Lower back", "my lower back", False, "Back", set()),
    ]),
    ("stomach-hips", "Stomach and hips", "Lower body", [
        ("stomach", "Stomach", "my stomach", False, "Belly", {"massage", "ice"}),
        ("lower-belly", "Lower belly or bladder", "my lower belly", False, "Belly", {"massage", "ice"}),
        ("left-hip", "Left hip", "my left hip", False, "Hips", set()),
        ("right-hip", "Right hip", "my right hip", False, "Hips", set()),
        ("both-hips", "Both hips", "both of my hips", True, "Hips", set()),
    ]),
    ("legs-feet", "Legs and feet", "Lower body", [
        ("left-leg", "Left leg", "my left leg", False, "Left side", set()),
        ("left-knee", "Left knee", "my left knee", False, "Left side", set()),
        ("left-foot", "Left foot", "my left foot", False, "Left side", set()),
        ("right-leg", "Right leg", "my right leg", False, "Right side", set()),
        ("right-knee", "Right knee", "my right knee", False, "Right side", set()),
        ("right-foot", "Right foot", "my right foot", False, "Right side", set()),
        ("both-legs", "Both legs", "both of my legs", True, "Both sides", set()),
        ("both-feet", "Both feet", "both of my feet", True, "Both sides", set()),
    ]),
    ("skin-pressure", "Skin and pressure spots", "Other", [
        ("tailbone", "Tailbone", "my tailbone", False, "Pressure spots", {"massage", "ice"}),
        ("heels", "Heels", "my heels", True, "Pressure spots", {"ice"}),
        ("back-of-head", "Back of my head", "the back of my head", False, "Pressure spots", {"ice"}),
        ("tube-site", "Feeding tube site", "my feeding tube site", False, "Medical devices", {"massage", "heat"}),
        ("mask-skin", "Skin under my mask", "the skin under my mask", False, "Medical devices", {"massage", "heat", "ice"}),
        ("iv-site", "IV or line site", "my IV site", False, "Medical devices", {"massage", "heat"}),
    ]),
    ("all-over", "All over", "Other", [
        ("all-over", "Whole body", "", False, "Whole body", {"ice", "heat"}),
    ]),
]

# (slug, label, group, sentence template; {obj} is it/them)
PAIN_ACTIONS = [
    ("medicine", "Pain medicine", "Treat it", "Please give me pain medicine."),
    ("ice", "Ice pack", "Treat it", "Please put an ice pack on {obj}."),
    ("heat", "Warm pack", "Treat it", "Please put a warm pack on {obj}."),
    ("massage", "Gentle massage", "Treat it", "Please massage {obj} gently."),
    ("reposition", "Reposition me", "Move or check", "Please help me reposition."),
    ("check", "Check it", "Move or check", "Please check {obj}."),
    ("nurse", "Call the nurse", "Tell someone", "Please call the nurse."),
    ("inform", "Just letting you know", "Tell someone", "I just wanted you to know."),
]

PAIN_SEVERITY = [
    ("mild", "A little", "How bad", " a little"),
    ("moderate", "Quite a bit", "How bad", " quite a bit"),
    ("severe", "Very badly", "How bad", " very badly"),
    ("skip", "Skip rating", "No rating", ""),
]


def _pain_sentence(subject: str, plural: bool, severity: str) -> str:
    if not subject:  # whole body
        return f"I hurt all over{severity}."
    verb = "hurt" if plural else "hurts"
    return f"{subject[0].upper()}{subject[1:]} {verb}{severity}."


def build_pain(home: Node) -> None:
    pain = child(home, "pain", "Pain", "Medical", "Where does it hurt?", icon="pain")
    for r_slug, r_label, r_group, parts in PAIN_REGIONS:
        region = child(pain, r_slug, r_label, r_group, "Which part hurts?")
        for p_slug, p_label, subject, plural, p_group, excluded in parts:
            part = child(region, p_slug, p_label, p_group, "What would help?")
            obj = "me" if not subject else ("them" if plural else "it")
            for a_slug, a_label, a_group, tmpl in PAIN_ACTIONS:
                if a_slug in excluded:
                    continue
                if not subject and a_slug == "check":
                    tmpl = "Please check on me."
                action = child(part, a_slug, a_label, a_group, "How bad is the pain?")
                for s_slug, s_label, s_group, adverb in PAIN_SEVERITY:
                    sentence = f"{_pain_sentence(subject, plural, adverb)} {tmpl.format(obj=obj)}"
                    child(action, s_slug, s_label, s_group, phrase=sentence, urgent=(s_slug == "severe"))


# ---------------------------------------------------------------------------------------
# BREATHING ASSISTANCE: need -> how soon
# ---------------------------------------------------------------------------------------
BREATHING = [
    # (slug, label, group, sentence, ask_urgency)
    ("suction-mouth", "Suction my mouth", "Airway", "Please suction my mouth.", True),
    ("suction-trach", "Suction my trach", "Airway", "Please suction my tracheostomy.", True),
    ("cough", "Help me cough", "Airway", "Please help me cough.", True),
    ("mucus", "Mucus I can't clear", "Airway", "I have mucus I can't clear.", True),
    ("adjust-mask", "Adjust my mask", "Mask and machine", "Please adjust my breathing mask.", True),
    ("mask-tight", "Mask too tight", "Mask and machine", "My mask is too tight.", True),
    ("mask-leak", "Mask is leaking", "Mask and machine", "My mask is leaking.", True),
    ("mask-break", "Mask off briefly", "Mask and machine", "Please take my mask off for a moment.", True),
    ("machine", "Check breathing machine", "Mask and machine", "Please check my breathing machine.", True),
    ("oxygen", "Check my oxygen", "Mask and machine", "Please check my oxygen.", True),
    ("alarm", "Machine is alarming", "Mask and machine", "My breathing machine is alarming.", True),
    ("short-of-breath", "Short of breath", "How I feel", "I am short of breath.", True),
    ("not-enough-air", "Not enough air", "How I feel", "I feel like I can't get enough air.", True),
    ("anxious", "Anxious about breathing", "How I feel", "I feel anxious about my breathing.", True),
    ("better", "Breathing better now", "How I feel", "My breathing feels better now.", False),
    ("raise-head", "Raise my head", "Position", "Please raise the head of my bed.", True),
    ("sit-up", "Sit me up", "Position", "Please sit me up to help my breathing.", True),
    ("fan", "Fan on my face", "Position", "Please point a fan toward my face.", True),
]
URGENCY = [
    ("now", "Right now", "How soon", "I need this right now."),
    ("soon", "When you can", "How soon", "It can wait a few minutes."),
]


def build_breathing(home: Node) -> None:
    b = child(home, "breathing", "Breathing assistance", "Medical", "What do you need for your breathing?", icon="breathing")
    for slug, label, group, sentence, ask in BREATHING:
        if not ask:
            child(b, slug, label, group, phrase=sentence)
            continue
        n = child(b, slug, label, group, "How soon do you need it?")
        for u_slug, u_label, u_group, extra in URGENCY:
            child(n, u_slug, u_label, u_group, phrase=f"{sentence} {extra}", urgent=(u_slug == "now"))


# ---------------------------------------------------------------------------------------
# FOOD AND WATER
# ---------------------------------------------------------------------------------------
DRINKS = [
    ("water", "Water", "Cold drinks", "some water"), ("ice-chips", "Ice chips", "Cold drinks", "some ice chips"),
    ("juice", "Juice", "Cold drinks", "some juice"), ("milk", "Milk", "Cold drinks", "some milk"),
    ("ginger-ale", "Ginger ale", "Cold drinks", "some ginger ale"),
    ("coffee", "Coffee", "Warm drinks", "some coffee"), ("tea", "Tea", "Warm drinks", "some tea"),
    ("shake", "Nutrition shake", "Nutrition", "a nutrition shake"),
]
DRINK_WAYS = [
    ("sips", "A few sips", "How", "Just a few sips, please."),
    ("straw", "With a straw", "How", "With a straw, please."),
    ("cup", "A full cup", "How", "A full cup, please."),
]
FOODS = [
    ("yogurt", "Yogurt", "Sweet", "some yogurt"), ("pudding", "Pudding", "Sweet", "some pudding"),
    ("applesauce", "Applesauce", "Sweet", "some applesauce"), ("ice-cream", "Ice cream", "Sweet", "some ice cream"),
    ("smoothie", "Smoothie", "Sweet", "a smoothie"),
    ("soup", "Soup", "Savory", "some soup"), ("mashed-potatoes", "Mashed potatoes", "Savory", "some mashed potatoes"),
    ("oatmeal", "Oatmeal", "Savory", "some oatmeal"),
]
PORTIONS = [("small", "Small portion", "How much", "a small portion"), ("regular", "Regular portion", "How much", "a regular portion")]
MOUTH = [
    ("dry-mouth", "My mouth is dry", "Mouth care", "My mouth is dry. Please moisten it."),
    ("lips", "Moisten my lips", "Mouth care", "Please moisten my lips."),
    ("clean-mouth", "Clean my mouth", "Mouth care", "Please clean my mouth."),
    ("brush-teeth", "Brush my teeth", "Mouth care", "Please brush my teeth."),
    ("drool", "Wipe my mouth", "Mouth care", "Please wipe my mouth and chin."),
    ("swallowing", "Trouble swallowing", "Swallowing", "I am having trouble swallowing."),
    ("stuck", "Something is stuck", "Swallowing", "I feel like something is stuck in my throat."),
    ("choking", "I am choking", "Swallowing", "I am choking. Help me now!"),
]
TUBE = [
    ("check-tube", "Check my feeding tube", "Feeding tube", "Please check my feeding tube."),
    ("pause-feed", "Pause my feeding", "Feeding tube", "Please pause my tube feeding."),
    ("flush", "Flush my tube", "Feeding tube", "Please flush my feeding tube."),
    ("full", "I feel full", "How I feel", "I feel full."),
    ("nauseous", "I feel sick", "How I feel", "I feel nauseous."),
    ("bloated", "I feel bloated", "How I feel", "My stomach feels bloated."),
]
NOT_NOW = [
    ("no-food", "Not hungry", "Not now", "I don't want to eat right now."),
    ("no-drink", "Not thirsty", "Not now", "I'm not thirsty right now."),
    ("done", "I'm finished", "Not now", "I'm finished eating. Thank you."),
]


def build_food(home: Node) -> None:
    f = child(home, "food-water", "Food and water", "Daily needs", "What would you like?", icon="food")
    drinks = child(f, "drink", "Something to drink", "Eat or drink", "What would you like to drink?")
    for slug, label, group, obj in DRINKS:
        d = child(drinks, slug, label, group, "How would you like it?")
        for w_slug, w_label, w_group, tail in DRINK_WAYS:
            if slug == "ice-chips" and w_slug == "straw":
                w_label, tail = "On a spoon", "On a spoon, please."
                w_slug = "spoon"
            child(d, w_slug, w_label, w_group, phrase=f"I would like {obj}. {tail}")
    food = child(f, "eat", "Something to eat", "Eat or drink", "What would you like to eat?")
    for slug, label, group, obj in FOODS:
        d = child(food, slug, label, group, "How much?")
        for p_slug, p_label, p_group, tail in PORTIONS:
            child(d, p_slug, p_label, p_group, phrase=f"I am hungry. I would like {obj}, {tail} please.")
    mouth = child(f, "mouth", "Mouth and swallowing", "Mouth and tube", "What is the problem?")
    for slug, label, group, sentence in MOUTH:
        child(mouth, slug, label, group, phrase=sentence, urgent=(slug == "choking"))
    tube = child(f, "tube", "Tube feeding", "Mouth and tube", "What about your tube feeding?")
    for slug, label, group, sentence in TUBE:
        child(tube, slug, label, group, phrase=sentence)
    nn = child(f, "not-now", "Not right now", "Other", "What do you want to tell us?")
    for slug, label, group, sentence in NOT_NOW:
        child(nn, slug, label, group, phrase=sentence)


# ---------------------------------------------------------------------------------------
# COMFORT
# ---------------------------------------------------------------------------------------
POSITION = [
    ("turn-left", "Turn me to my left", "Turn or sit", "Please turn me onto my left side."),
    ("turn-right", "Turn me to my right", "Turn or sit", "Please turn me onto my right side."),
    ("on-back", "On my back", "Turn or sit", "Please lay me on my back."),
    ("sit-up", "Sit me up", "Turn or sit", "Please sit me up."),
    ("lie-down", "Lay me down", "Turn or sit", "Please lay me down flatter."),
    ("move-up", "Move me up in bed", "Adjust", "Please move me up in the bed."),
    ("raise-legs", "Raise my legs", "Adjust", "Please raise my legs."),
    ("lower-legs", "Lower my legs", "Adjust", "Please lower my legs."),
    ("straighten", "Straighten my body", "Adjust", "Please straighten my body. I feel twisted."),
    ("chair", "Get me into my chair", "Adjust", "I would like to get into my chair."),
]
PILLOWS = [
    ("head", "Head", "Upper body", "my head"), ("neck", "Neck", "Upper body", "my neck"),
    ("left-arm", "Left arm", "Upper body", "my left arm"), ("right-arm", "Right arm", "Upper body", "my right arm"),
    ("back", "Back", "Upper body", "my back"),
    ("knees", "Under my knees", "Lower body", "my knees"), ("between-knees", "Between my knees", "Lower body", "between my knees"),
    ("heels", "Under my heels", "Lower body", "my heels"),
]
TEMPERATURE = [
    ("too-hot", "I'm too hot", "How I feel", "I am too hot."), ("too-cold", "I'm too cold", "How I feel", "I am too cold."),
    ("add-blanket", "Add a blanket", "Blankets and fan", "Please put a blanket on me."),
    ("remove-blanket", "Remove a blanket", "Blankets and fan", "Please take a blanket off me."),
    ("fan-on", "Fan on", "Blankets and fan", "Please turn the fan on."),
    ("fan-off", "Fan off", "Blankets and fan", "Please turn the fan off."),
]
ROOM = [
    ("lights-on", "Lights on", "Light", "Please turn the lights on."), ("lights-off", "Lights off", "Light", "Please turn the lights off."),
    ("dim", "Dim the lights", "Light", "Please dim the lights."),
    ("curtains-open", "Open the curtains", "Light", "Please open the curtains."),
    ("curtains-close", "Close the curtains", "Light", "Please close the curtains."),
    ("tv-on", "TV on", "TV and music", "Please turn the TV on."), ("tv-off", "TV off", "TV and music", "Please turn the TV off."),
    ("channel", "Change the channel", "TV and music", "Please change the TV channel."),
    ("music-on", "Play music", "TV and music", "Please play some music."),
    ("music-off", "Stop the music", "TV and music", "Please stop the music."),
    ("quiet", "Quieter please", "Noise", "Please make it quieter in here."),
    ("door", "Close the door", "Noise", "Please close the door."),
]
HYGIENE = [
    ("bathroom", "I need the bathroom", "Toilet", "I need to use the bathroom."),
    ("changed", "I need to be changed", "Toilet", "I need to be changed, please."),
    ("wash-face", "Wash my face", "Washing", "Please wash my face."),
    ("brush-hair", "Brush my hair", "Washing", "Please brush my hair."),
    ("wipe-eyes", "Wipe my eyes", "Washing", "Please wipe my eyes."),
    ("shave", "Shave me", "Washing", "Please shave me."),
    ("lotion", "Lotion for dry skin", "Washing", "My skin is dry. Please put lotion on it."),
    ("nose", "Wipe my nose", "Washing", "Please wipe my nose."),
]
ITCH_PARTS = [
    ("scalp", "Scalp", "Head", "my scalp"), ("face", "Face", "Head", "my face"), ("nose", "Nose", "Head", "my nose"),
    ("ears", "Ears", "Head", "my ears"), ("neck", "Neck", "Body", "my neck"), ("back", "Back", "Body", "my back"),
    ("chest", "Chest", "Body", "my chest"), ("left-arm", "Left arm", "Arms", "my left arm"),
    ("right-arm", "Right arm", "Arms", "my right arm"), ("hands", "Hands", "Arms", "my hands"),
    ("left-leg", "Left leg", "Legs", "my left leg"), ("right-leg", "Right leg", "Legs", "my right leg"),
    ("feet", "Feet", "Legs", "my feet"), ("under-mask", "Under my mask", "Devices", "under my mask"),
]
ITCH_ACTIONS = [("scratch", "Scratch it", "What to do", "Please scratch it gently."),
                ("cream", "Put cream on it", "What to do", "Please put some anti-itch cream on it.")]
GLASSES = [
    ("glasses-on", "Glasses on", "Glasses", "Please put my glasses on."),
    ("glasses-off", "Glasses off", "Glasses", "Please take my glasses off."),
    ("clean-glasses", "Clean my glasses", "Glasses", "Please clean my glasses."),
    ("hearing-in", "Hearing aids in", "Hearing aids", "Please put my hearing aids in."),
    ("hearing-out", "Hearing aids out", "Hearing aids", "Please take my hearing aids out."),
]
FEELINGS = [
    ("scared", "I'm scared", "Hard feelings", "I am scared."), ("lonely", "I'm lonely", "Hard feelings", "I feel lonely."),
    ("sad", "I'm sad", "Hard feelings", "I feel sad."), ("frustrated", "I'm frustrated", "Hard feelings", "I am frustrated."),
    ("bored", "I'm bored", "Hard feelings", "I am bored."),
    ("tired", "I'm tired", "Rest", "I am tired."), ("cant-sleep", "I can't sleep", "Rest", "I can't sleep."),
    ("ok", "I'm okay", "Good feelings", "I am okay."), ("happy", "I'm happy", "Good feelings", "I am happy."),
]
CONVERSATION = [
    ("yes", "Yes", "Answers", "Yes."), ("no", "No", "Answers", "No."), ("dont-know", "I don't know", "Answers", "I don't know."),
    ("repeat", "Please repeat that", "Talking", "Please repeat that."), ("wait", "Please wait", "Talking", "Please wait a moment."),
    ("yes-no-questions", "Ask me yes/no questions", "Talking", "Please ask me yes or no questions."),
    ("talk", "Talk to me", "Company", "Please talk with me for a while."), ("read", "Read to me", "Company", "Please read to me."),
    ("stay", "Stay with me", "Company", "Please stay with me."), ("rest", "Let me rest", "Company", "I would like to rest now."),
    ("thanks", "Thank you", "Kind words", "Thank you."), ("love", "I love you", "Kind words", "I love you."),
]


def _leaves(parent: Node, items, urgent_slugs: set[str] = frozenset()) -> None:
    for slug, label, group, sentence in items:
        child(parent, slug, label, group, phrase=sentence, urgent=slug in urgent_slugs)


def build_comfort(home: Node) -> None:
    c = child(home, "comfort", "Comfort", "Daily needs", "What would make you more comfortable?", icon="comfort")
    pos = child(c, "position", "Move my body", "My body", "How should we move you?")
    _leaves(pos, POSITION)
    pil = child(pos, "pillow", "Add a pillow", "Pillows", "Where should the pillow go?")
    for slug, label, group, where in PILLOWS:
        prep = "" if where.startswith("between") else "under "
        child(pil, slug, label, group, phrase=f"Please put a pillow {prep}{where}.")
    itch = child(c, "itch", "Itch", "My body", "Where does it itch?")
    for slug, label, group, where in ITCH_PARTS:
        part = child(itch, slug, label, group, "What should we do?")
        lead = f"It itches {where}." if where.startswith("under") else f"{where[0].upper()}{where[1:]} itches."
        for a_slug, a_label, a_group, tail in ITCH_ACTIONS:
            child(part, a_slug, a_label, a_group, phrase=f"{lead} {tail}")
    hyg = child(c, "hygiene", "Washing and toilet", "My body", "What do you need?")
    _leaves(hyg, HYGIENE)
    temp = child(c, "temperature", "Temperature", "My room", "How can we help with temperature?")
    _leaves(temp, TEMPERATURE)
    room = child(c, "room", "Lights, TV and noise", "My room", "What should we change?")
    _leaves(room, ROOM)
    gl = child(c, "glasses-hearing", "Glasses and hearing aids", "My room", "What do you need?")
    _leaves(gl, GLASSES)
    feel = child(c, "feelings", "How I feel", "Feelings and talk", "How do you feel?")
    _leaves(feel, FEELINGS)
    conv = child(c, "conversation", "Quick replies", "Feelings and talk", "What do you want to say?")
    _leaves(conv, CONVERSATION)


# ---------------------------------------------------------------------------------------
# CALL CAREGIVER: who -> why
# ---------------------------------------------------------------------------------------
PEOPLE = [
    ("nurse", "My nurse", "Care team", "my nurse"), ("doctor", "My doctor", "Care team", "my doctor"),
    ("caregiver", "My caregiver", "Care team", "my caregiver"),
    ("respiratory-therapist", "Respiratory therapist", "Care team", "the respiratory therapist"),
    ("partner", "My spouse or partner", "Family and friends", "my spouse or partner"),
    ("children", "My children", "Family and friends", "my children"),
    ("family", "My family", "Family and friends", "my family"), ("friend", "A friend", "Family and friends", "a friend"),
    ("chaplain", "Chaplain", "Support", "the chaplain"), ("social-worker", "Social worker", "Support", "the social worker"),
]
REASONS = [
    ("come-now", "Come right now", "Come here", "Please get {who} to come right now."),
    ("come-soon", "Come when they can", "Come here", "Please ask {who} to come when they can."),
    ("help", "I need their help", "Come here", "I need help from {who}."),
    ("talk", "I want to talk", "Talk", "I want to talk with {who}."),
    ("phone", "Call them on the phone", "Talk", "Please call {who} on the phone for me."),
    ("visit", "I'd like a visit", "Talk", "I would like {who} to visit me."),
]


def build_caregiver(home: Node) -> None:
    cg = child(home, "call-caregiver", "Call caregiver", "People", "Who should we call?", icon="caregiver")
    for slug, label, group, who in PEOPLE:
        p = child(cg, slug, label, group, "Why do you want them?")
        for r_slug, r_label, r_group, tmpl in REASONS:
            child(p, r_slug, r_label, r_group, phrase=tmpl.format(who=who), urgent=(r_slug == "come-now"))


# ---------------------------------------------------------------------------------------
@dataclass
class Catalog:
    root: Node
    by_id: dict[str, Node]

    def get(self, node_id: str) -> Node:
        return self.by_id[node_id]

    def leaves(self) -> list[Node]:
        return [n for n in self.by_id.values() if n.is_leaf and n is not self.root]

    def search(self, q: str, limit: int = 50) -> list[Node]:
        words = [w for w in q.lower().split() if w]
        out = []
        for n in self.leaves():
            hay = f"{n.phrase} {n.title}".lower()
            if all(w in hay for w in words):
                out.append(n)
                if len(out) >= limit:
                    break
        return out

    def stats(self) -> dict:
        if getattr(self, "_stats", None) is None:
            self._stats = self._compute_stats()
        return self._stats

    def _compute_stats(self) -> dict:
        leaves = self.leaves()
        depth = [len(n.path()) - 1 for n in leaves]
        per_cat = {c.label: sum(1 for leaf in leaves if leaf.path()[1] is c) for c in self.root.children}
        return {"phrases": len(leaves), "unique_sentences": len({leaf.phrase for leaf in leaves}),
                "nodes": len(self.by_id), "max_depth": max(depth), "mean_depth": round(sum(depth) / len(depth), 2),
                "per_category": per_cat}


@lru_cache(maxsize=1)
def build_catalog() -> Catalog:
    home = Node(id="home", label="Home", prompt="What do you need?")
    build_pain(home)
    build_breathing(home)
    build_food(home)
    build_comfort(home)
    build_caregiver(home)
    by_id: dict[str, Node] = {}

    def walk(n: Node) -> None:
        if n.id in by_id:
            raise ValueError(f"duplicate node id {n.id}")
        by_id[n.id] = n
        for c in n.children:
            walk(c)

    walk(home)
    return Catalog(home, by_id)
