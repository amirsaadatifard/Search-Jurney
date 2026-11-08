"""Fictitious content of the sample website ("Kiln & Clay Studio")."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

SITE_NAME: Final = "Kiln & Clay Studio"


@dataclass(frozen=True, slots=True)
class NavLink:
    path: str
    label: str


@dataclass(frozen=True, slots=True)
class Product:
    slug: str
    name: str
    price: str
    summary: str
    hue: int  # colour of the placeholder image


@dataclass(frozen=True, slots=True)
class ArticleSection:
    anchor: str
    heading: str
    paragraphs: tuple[str, ...]


NAVIGATION: Final[tuple[NavLink, ...]] = (
    NavLink("/", "Home"),
    NavLink("/products", "Products"),
    NavLink("/article", "Journal"),
    NavLink("/about", "About"),
    NavLink("/contact", "Contact"),
)

PRODUCTS: Final[tuple[Product, ...]] = (
    Product(
        "speckled-mug", "Speckled Stoneware Mug", "24.00", "Wheel-thrown, 350 ml, oat glaze.", 28
    ),
    Product("tall-mug", "Tall Latte Mug", "27.00", "A slim 420 ml mug with a wide handle.", 200),
    Product("espresso-cups", "Espresso Cup Pair", "30.00", "Two 90 ml cups with saucers.", 12),
    Product("breakfast-bowl", "Breakfast Bowl", "22.00", "Deep 600 ml bowl, satin white.", 160),
    Product("ramen-bowl", "Ramen Bowl", "34.00", "Wide rim, iron-speckled charcoal glaze.", 250),
    Product("dinner-plate", "Dinner Plate", "29.00", "27 cm plate with a raised edge.", 40),
    Product("side-plate", "Side Plate", "18.00", "19 cm plate, pairs with the dinner plate.", 95),
    Product("planter-small", "Small Planter", "26.00", "12 cm planter with drainage hole.", 130),
    Product("planter-large", "Large Planter", "48.00", "22 cm planter and matching tray.", 310),
    Product(
        "pour-over", "Pour-over Dripper", "38.00", "Single-cup dripper, fits standard filters.", 20
    ),
    Product("butter-dish", "Butter Dish", "32.00", "Lidded dish in a soft celadon glaze.", 175),
    Product("bud-vase", "Bud Vase", "21.00", "Narrow-necked vase for a single stem.", 280),
)

FEATURES: Final[tuple[tuple[str, str], ...]] = (
    ("Made in small batches", "Every piece is thrown, trimmed and glazed by hand in our studio."),
    ("Built for daily use", "Stoneware fired to 1240 °C: dishwasher safe and hard to chip."),
    ("Glazes mixed in-house", "Lead-free glazes we develop and test ourselves, batch by batch."),
    ("Repairs welcome", "Chipped a favourite mug? Bring it back and we will see what we can do."),
)

TESTIMONIALS: Final[tuple[tuple[str, str], ...]] = (
    (
        "The mugs are heavier than they look and keep coffee warm for ages.",
        "Mara, regular customer",
    ),
    (
        "I took the beginners' workshop and left with three bowls I actually use.",
        "Jonas, workshop guest",
    ),
    ("Careful packaging and a handwritten note. It felt like a gift.", "Elif, online order"),
)

ARTICLE_TITLE: Final = "From Clay to Cup: How a Stoneware Mug Is Made"
ARTICLE_SECTIONS: Final[tuple[ArticleSection, ...]] = (
    ArticleSection(
        "wedging",
        "1. Preparing the clay",
        (
            "Every mug starts as a block of stoneware clay that has to be wedged before it can "
            "be used. Wedging is a kneading motion that pushes air out and lines up the clay "
            "particles. It looks simple and takes years to do well.",
            "Air pockets are the enemy. A bubble trapped in a wall expands in the kiln and can "
            "crack the piece, or in the worst case break its neighbours too. We wedge each ball "
            "about forty times and weigh it so that mugs from the same batch match.",
            "A standard mug uses roughly 450 grams of wet clay. By the time it has been thrown, "
            "trimmed, dried and fired twice, about a third of that weight is gone.",
        ),
    ),
    ArticleSection(
        "throwing",
        "2. Throwing on the wheel",
        (
            "The ball of clay is slapped onto the wheel head and centred. Centring is the step "
            "beginners struggle with most: the clay must spin without wobbling before anything "
            "else can happen. Steady hands and a braced elbow matter more than strength.",
            "Once centred, the potter opens the clay with both thumbs, compresses the floor so "
            "it will not crack, and pulls the walls upward in three or four passes. Each pull "
            "thins the wall and adds height. Water keeps the surface moving, but too much "
            "weakens the form.",
            "The shape is refined with a wooden rib, the rim is compressed with a strip of "
            "leather, and the mug is cut from the wheel with a wire. At this point it is too "
            "soft to pick up by hand, so it is lifted on a small board.",
        ),
    ),
    ArticleSection(
        "trimming",
        "3. Trimming and handles",
        (
            "After a day of slow drying the mug reaches a stage called leather-hard: firm "
            "enough to handle, soft enough to carve. It goes back on the wheel upside down and "
            "the base is trimmed to form a foot ring.",
            "Handles are pulled separately from a wet lug of clay, a bit like milking. A pulled "
            "handle has a natural taper that an extruded one cannot match. It is scored, "
            "slipped and attached, then the join is smoothed with a damp sponge.",
            "Handles crack more often than any other part, because they dry faster than the "
            "body. We wrap finished mugs loosely in plastic for two days so that everything "
            "shrinks at the same rate.",
        ),
    ),
    ArticleSection(
        "bisque",
        "4. The first firing",
        (
            "Bone-dry pieces are loaded into the kiln for the bisque firing, which climbs "
            "slowly to about 1000 °C over ten hours. The slow start lets the last moisture "
            "escape as steam without damage.",
            "Bisque-fired clay is hard but porous, like a terracotta flowerpot. That porosity is "
            "exactly what the next step needs: it lets the piece absorb glaze evenly.",
        ),
    ),
    ArticleSection(
        "glazing",
        "5. Glazing",
        (
            "Glaze is essentially powdered glass suspended in water, with clay to keep it in "
            "suspension and metal oxides for colour. Each mug is dipped for three seconds; the "
            "thirsty bisque pulls water in and leaves an even coat of powder on the surface.",
            "The foot is wiped clean, because any glaze left there would weld the mug to the "
            "kiln shelf. Small differences in thickness create the variation in colour that "
            "makes handmade pottery recognisable.",
            "We test every new glaze batch on small tiles first. Raw materials vary from one "
            "delivery to the next, and a recipe that worked last year can come out matte, runny "
            "or the wrong colour altogether.",
        ),
    ),
    ArticleSection(
        "glaze-firing",
        "6. The glaze firing",
        (
            "The second firing reaches 1240 °C. At that temperature the clay body vitrifies, "
            "meaning it becomes dense and waterproof, and the glaze melts into a smooth layer "
            "of glass bonded to the surface.",
            "Cooling takes longer than heating. Opening the kiln early risks dunting, a crack "
            "caused by thermal shock, so we wait a full day and a half. Opening a kiln never "
            "stops feeling like opening a present.",
            "Finished mugs are checked for sharp spots, the feet are sanded smooth, and each "
            "one is filled with water overnight to confirm it does not seep.",
        ),
    ),
    ArticleSection(
        "care",
        "7. Looking after stoneware",
        (
            "Stoneware is forgiving. It can go in the dishwasher and the microwave, and it does "
            "not stain easily. The one thing it dislikes is sudden temperature change: do not "
            "pour boiling water into a mug that has just come out of the freezer.",
            "With ordinary use a well-made mug lasts for decades. Many of ours are still in "
            "daily service more than ten years after they left the studio.",
        ),
    ),
)

TEAM: Final[tuple[tuple[str, str, int], ...]] = (
    ("Ines Varga", "Founder and lead potter", 30),
    ("Tomas Lind", "Glaze development", 190),
    ("Priya Nair", "Workshops and teaching", 330),
)

TIMELINE: Final[tuple[tuple[str, str], ...]] = (
    ("2012", "The studio opens in a converted garage with one second-hand kiln."),
    ("2015", "First weekend workshops; twelve students in the first month."),
    ("2018", "Move to the current building and installation of a second, larger kiln."),
    ("2021", "Online shop launched; orders now ship across the region."),
    ("2024", "All studio electricity switched to renewable sources."),
)

OPENING_HOURS: Final[tuple[tuple[str, str], ...]] = (
    ("Monday", "Closed"),
    ("Tuesday - Friday", "10:00 - 18:00"),
    ("Saturday", "10:00 - 16:00"),
    ("Sunday", "Workshops only"),
)
