"""Ticker Validator — three-layer validation (exclusion list → yfinance → LLM).

Ensures only real, actively traded stock tickers enter the watchlist.
"""

from __future__ import annotations

import yfinance as yf

from app.utils.logger import logger


class TickerValidator:
    """Three-layer validation: exclusion list → yfinance → LLM logic check."""

    # Common English words and abbreviations that look like tickers.
    # This list must be aggressive because transcripts contain thousands
    # of uppercase words that regex picks up as potential tickers.
    EXCLUSION_LIST: set[str] = {
        # ── Reddit / finance jargon ──
        "YOLO", "DD", "ATH", "IMO", "EOD", "WSB", "OP", "EDIT", "TLDR",
        "GAIN", "LOSS", "HOLD", "LONG", "PUMP", "DUMP", "MOON", "BEAR",
        "BULL", "CALL", "PUT", "OTM", "ITM", "DTE", "IV", "FD", "PUTS",
        "IPO", "ETF", "HODL", "FOMO", "DIPS", "RALLY",
        # ── Pronouns / determiners / prepositions / conjunctions ──
        "ON", "FOR", "AND", "OR", "IF", "BUT", "SO", "AT", "BY", "TO",
        "OF", "IN", "IT", "IS", "BE", "AS", "DO", "WE", "UP", "MY",
        "GO", "ME", "US", "THE", "AN", "AM", "NO", "HE", "YA",
        "ALL", "CAN", "HAS", "HER", "HIM", "HIS", "HOW", "ITS",
        "LET", "MAY", "NEW", "NOW", "OLD", "OUR", "OUT", "OWN",
        "SAY", "SHE", "TOO", "USE", "DAD", "MOM", "WAR", "FAR",
        "WHO", "WHY", "ANY", "FEW", "GOT", "HAD", "MAN", "MEN",
        "SET", "TRY", "WAY", "YET", "WAS", "DID", "HIT", "RAN",
        "SAT", "WON", "CUT", "BIT", "FIT", "GOD", "LOT", "SIT",
        "YES", "AGO", "AGE", "AID", "AIM", "AIR", "ART", "ASK",
        "ATE", "BAN", "BAR", "BAT", "BAY", "BED", "BIG", "BOX",
        "BOY", "BUS", "CAR", "COP", "COW", "CRY", "CUP", "DAY",
        "DIE", "DIG", "DOG", "DRY", "EAR", "EAT", "EGG", "END",
        "ERA", "EVE", "EYE", "FAN", "FAT", "FEE", "FEW", "FIG",
        "FLY", "FOG", "FOX", "FUN", "GAS", "GAP", "GUN", "GUY",
        "GYM", "HAT", "HAY", "HEN", "HID", "HOP", "HOT", "HUG",
        "ICE", "ILL", "INK", "INN", "JAM", "JAR", "JAW", "JET",
        "JOB", "JOY", "KEY", "KID", "KIT", "LAP", "LAW", "LAY",
        "LED", "LEG", "LID", "LIE", "LIP", "LOG", "LOW", "MAP",
        "MAT", "MIX", "MOB", "MUD", "MUG", "NAP", "NET", "NOR",
        "NOT", "NUT", "ODD", "OFF", "OIL", "ONE", "OWL", "PAN",
        "PAT", "PAY", "PEN", "PET", "PIE", "PIN", "PIT", "POT",
        "RAG", "RAM", "RAP", "RAT", "RAW", "RED", "RIB", "RID",
        "RIG", "RIM", "RIP", "ROB", "ROD", "ROT", "ROW", "RUB",
        "RUG", "RUN", "SAD", "SAP", "SAW", "SEA", "SEW", "SHY",
        "SIN", "SIP", "SIS", "SIX", "SKI", "SKY", "SLY", "SOB",
        "SOD", "SON", "SOP", "SOT", "SOW", "SPA", "SPY", "STY",
        "SUB", "SUM", "SUN", "TAB", "TAG", "TAN", "TAP", "TAR",
        "TAX", "TEA", "TEN", "THE", "TIE", "TIN", "TIP", "TOE",
        "TON", "TOP", "TOW", "TOY", "TUB", "TUG", "TWO", "URN",
        "VAN", "VAT", "VET", "VOW", "WEB", "WED", "WET", "WIG",
        "WIN", "WIT", "WOE", "WOK", "ZAP", "ZEN", "ZIP", "ZOO",
        # ── Common 4-5 letter English words found in transcripts ──
        "BACK", "BALL", "BAND", "BANK", "BASE", "BATH", "BEAN", "BEAT",
        "BEEN", "BELL", "BELT", "BEND", "BEST", "BILL", "BIND", "BIRD",
        "BITE", "BLOW", "BLUE", "BOAT", "BODY", "BOLD", "BOMB", "BOND",
        "BONE", "BOOK", "BOOT", "BORN", "BOSS", "BOTH", "BOWL", "BURN",
        "BUSY", "CAKE", "CALM", "CAME", "CAMP", "CARD", "CARE", "CASE",
        "CASH", "CAST", "CHAT", "CHIP", "CITY", "CLAP", "CLIP", "CLUB",
        "CLUE", "COAL", "COAT", "CODE", "COIN", "COLD", "COME", "COOK",
        "COOL", "COPY", "CORD", "CORE", "CORN", "COST", "CREW", "CROP",
        "CURE", "DARK", "DATA", "DATE", "DAWN", "DAYS", "DEAD", "DEAF",
        "DEAL", "DEAR", "DEBT", "DECK", "DEED", "DEEM", "DEEP", "DEER",
        "DENY", "DESK", "DIAL", "DIET", "DIRT", "DISH", "DISK", "DOES",
        "DONE", "DOOR", "DOSE", "DOWN", "DRAW", "DREW", "DROP", "DRUG",
        "DRUM", "DUAL", "DUCK", "DUDE", "DULL", "DUST", "DUTY", "EACH",
        "EARN", "EASE", "EAST", "EASY", "EDGE", "ELSE", "EVEN", "EVER",
        "EVIL", "EXAM", "FACE", "FACT", "FADE", "FAIL", "FAIR", "FAKE",
        "FALL", "FAME", "FARM", "FAST", "FATE", "FEAR", "FEED", "FEEL",
        "FELL", "FELT", "FILE", "FILL", "FILM", "FIND", "FINE", "FIRE",
        "FIRM", "FISH", "FIVE", "FLAG", "FLAT", "FLED", "FLEW", "FLIP",
        "FLOW", "FOLD", "FOLK", "FOND", "FOOD", "FOOL", "FOOT", "FORK",
        "FORM", "FORT", "FOUL", "FOUR", "FREE", "FROM", "FUEL", "FULL",
        "FUND", "FURY", "FUSE", "GAVE", "GEAR", "GIFT", "GIRL", "GIVE",
        "GLAD", "GLOW", "GLUE", "GOAL", "GOES", "GOLD", "GONE", "GOOD",
        "GRAB", "GRAY", "GREW", "GRID", "GRIN", "GRIP", "GROW", "GULF",
        "GUST", "GUYS", "HACK", "HAD", "HAIR", "HALF", "HALL", "HALT",
        "HAND", "HANG", "HARD", "HARM", "HATE", "HAUL", "HAVE", "HEAD",
        "HEAL", "HEAP", "HEAR", "HEAT", "HEEL", "HELD", "HELL", "HELP",
        "HERE", "HERO", "HIDE", "HIGH", "HIKE", "HILL", "HINT", "HIRE",
        "HOLE", "HOME", "HOOK", "HOPE", "HOST", "HOUR", "HUMP", "HUNG",
        "HUNT", "HURT", "ICON", "IDEA", "INTO", "IRON", "ISLE", "ITEM",
        "JACK", "JAIL", "JANE", "JOHN", "JOIN", "JOKE", "JUMP", "JUNE",
        "JURY", "JUST", "KEEN", "KEEP", "KEPT", "KICK", "KIDS", "KILL",
        "KIND", "KING", "KISS", "KNEE", "KNEW", "KNIT", "KNOB", "KNOT",
        "KNOW", "LACK", "LAID", "LAKE", "LAMP", "LAND", "LANE", "LAST",
        "LATE", "LAWN", "LEAD", "LEAF", "LEAN", "LEAP", "LEFT", "LEND",
        "LESS", "LIED", "LIFE", "LIFT", "LIKE", "LIMB", "LIME", "LINE",
        "LINK", "LIST", "LIVE", "LOAD", "LOAN", "LOCK", "LOGO", "LONE",
        "LOOK", "LORD", "LOSE", "LOST", "LOTS", "LOUD", "LOVE", "LUCK",
        "LUNG", "LURE", "MADE", "MAIL", "MAIN", "MAKE", "MALE", "MALL",
        "MANY", "MARE", "MARK", "MARS", "MASK", "MASS", "MATE", "MEAL",
        "MEAN", "MEAT", "MEET", "MELT", "MEMO", "MENU", "MERE", "MESS",
        "MILD", "MILE", "MILK", "MILL", "MIND", "MINE", "MINT", "MISS",
        "MODE", "MOOD", "MORE", "MOST", "MOVE", "MUCH", "MUST", "MYTH",
        "NAIL", "NAME", "NAVY", "NEAR", "NEAT", "NECK", "NEED", "NEWS",
        "NEXT", "NICE", "NINE", "NODE", "NONE", "NOON", "NORM", "NOSE",
        "NOTE", "NOUN", "OBEY", "ODDS", "OKAY", "ONCE", "ONLY", "ONTO",
        "OPEN", "ORAL", "OURS", "OVER", "OVEN", "PACE", "PACK", "PAGE",
        "PAID", "PAIN", "PAIR", "PALE", "PALM", "PANT", "PARK", "PART",
        "PASS", "PAST", "PATH", "PEAK", "PEEL", "PEER", "PICK", "PILE",
        "PINE", "PINK", "PIPE", "PLAN", "PLAY", "PLEA", "PLOT", "PLUG",
        "PLUS", "POEM", "POET", "POLE", "POLL", "POND", "POOL", "POOR",
        "POPE", "PORK", "PORT", "POSE", "POST", "POUR", "PRAY", "PREY",
        "PULL", "PUMP", "PURE", "PUSH", "QUIT", "RACE", "RAGE", "RAID",
        "RAIL", "RAIN", "RANK", "RARE", "RATE", "READ", "REAR", "RELY",
        "RENT", "REST", "RICE", "RICH", "RIDE", "RING", "RISE", "RISK",
        "ROAD", "ROCK", "RODE", "ROLE", "ROLL", "ROOF", "ROOM", "ROOT",
        "ROPE", "ROSE", "RUIN", "RULE", "RUSH", "SAFE", "SAID", "SAIL",
        "SAKE", "SALE", "SALT", "SAND", "SANG", "SAVE", "SEAL", "SEED",
        "SEEK", "SEEM", "SEEN", "SELF", "SELL", "SEND", "SENT", "SHED",
        "SHIP", "SHOP", "SHOT", "SHOW", "SHUT", "SICK", "SIDE", "SIGH",
        "SIGN", "SILK", "SING", "SINK", "SITE", "SIZE", "SKIN", "SLAM",
        "SLAP", "SLID", "SLIM", "SLIP", "SLOT", "SLOW", "SNAP", "SNOW",
        "SOAP", "SOCK", "SOFT", "SOIL", "SOLD", "SOLE", "SOMA", "SOME",
        "SONG", "SOON", "SORT", "SOUL", "SPIN", "SPOT", "STAR", "STAY",
        "STEM", "STEP", "STIR", "STOP", "SUCH", "SUIT", "SUNG", "SURE",
        "SWIM", "SWAM", "SWAP", "TAIL", "TAKE", "TALE", "TALK", "TALL",
        "TANK", "TAPE", "TASK", "TEAM", "TEAR", "TECH", "TELL", "TEND",
        "TENT", "TERM", "TEST", "TEXT", "THAN", "THAT", "THEM", "THEN",
        "THEY", "THIN", "THIS", "THUS", "TICK", "TIDE", "TIDY", "TIED",
        "TIER", "TILL", "TIME", "TINY", "TIRE", "TOAD", "TOES", "TOLD",
        "TOLL", "TOMB", "TONE", "TOOK", "TOOL", "TOPS", "TORE", "TORN",
        "TOSS", "TOUR", "TOWN", "TRAP", "TRAY", "TREE", "TREK", "TRIM",
        "TRIO", "TRIP", "TRUE", "TUBE", "TUCK", "TUNE", "TURN", "TWIN",
        "TYPE", "UGLY", "UNDO", "UNIT", "UPON", "URGE", "USED", "USER",
        "VALE", "VARY", "VAST", "VERB", "VERY", "VEST", "VICE", "VIEW",
        "VINE", "VOID", "VOLT", "VOTE", "WADE", "WAGE", "WAIT", "WAKE",
        "WALK", "WALL", "WAND", "WANT", "WARD", "WARM", "WARN", "WARP",
        "WASH", "WAVE", "WEAK", "WEAR", "WEED", "WEEK", "WELL", "WENT",
        "WERE", "WEST", "WHAT", "WHEN", "WHICH", "WIDE", "WIFE", "WILD",
        "WILL", "WIND", "WINE", "WING", "WIPE", "WIRE", "WISE", "WISH",
        "WITH", "WOKE", "WOLF", "WOOD", "WOOL", "WORD", "WORE", "WORN",
        "WRAP", "WRIT", "YARD", "YEAR", "YOUR", "ZERO", "ZONE",
        # ── 5-letter common English words ──
        "ABOUT", "ABOVE", "AFTER", "AGAIN", "AGREE", "AHEAD", "ALLOW",
        "ALONE", "ALONG", "AMONG", "ANGRY", "APART", "APPLY", "ARENA",
        "ARGUE", "ARISE", "ASIDE", "ASSET", "AVOID", "AWARD", "AWARE",
        "BADLY", "BASIC", "BASIS", "BATCH", "BEGIN", "BEING", "BELOW",
        "BLACK", "BLADE", "BLAME", "BLANK", "BLAST", "BLAZE", "BLEED",
        "BLEND", "BLIND", "BLOCK", "BLOOD", "BLOWN", "BOARD", "BOOST",
        "BONUS", "BOUND", "BRAIN", "BRAND", "BRAVE", "BREAD", "BREAK",
        "BREED", "BRICK", "BRIEF", "BRING", "BROAD", "BROKE", "BROWN",
        "BRUSH", "BUILD", "BUILT", "BUNCH", "BURST", "BUYER", "CABLE",
        "CARRY", "CATCH", "CAUSE", "CHAIN", "CHAIR", "CHEAP", "CHECK",
        "CHEST", "CHIEF", "CHILD", "CHINA", "CHUNK", "CIVIL", "CLAIM",
        "CLASS", "CLEAN", "CLEAR", "CLIMB", "CLING", "CLOCK", "CLOSE",
        "CLOUD", "COACH", "COAST", "COLOR", "COUNT", "COULD", "COURT",
        "COVER", "CRACK", "CRAFT", "CRASH", "CRAZY", "CREAM", "CRIME",
        "CROSS", "CROWD", "CRUSH", "CURVE", "CYCLE", "CYBER", "DAILY",
        "DANCE", "DEATH", "DEBUT", "DELAY", "DEPTH", "DIRTY", "DOUBT",
        "DOZEN", "DRAFT", "DRAIN", "DRAMA", "DRANK", "DRAWN", "DREAM",
        "DRESS", "DRIED", "DRIFT", "DRILL", "DRINK", "DRIVE", "DROVE",
        "DYING", "EAGER", "EARLY", "EARTH", "EIGHT", "ELECT", "ELITE",
        "EMPTY", "ENEMY", "ENJOY", "ENTER", "ENTRY", "EQUAL", "ERROR",
        "EVENT", "EVERY", "EXACT", "EXIST", "EXTRA", "FAITH", "FALSE",
        "FANCY", "FAULT", "FEAST", "FEWER", "FIBER", "FIELD", "FIFTH",
        "FIFTY", "FIGHT", "FINAL", "FIRST", "FIXED", "FLAME", "FLASH",
        "FLEET", "FLESH", "FLIES", "FLOAT", "FLOOD", "FLOOR", "FLUSH",
        "FOCUS", "FORCE", "FOUND", "FRAME", "FRESH", "FRONT", "FRUIT",
        "FULLY", "FUNNY", "GAINS", "GIVEN", "GLASS", "GLOBE", "GLORY",
        "GOING", "GRACE", "GRADE", "GRAIN", "GRAND", "GRANT", "GRAPH",
        "GRASP", "GRASS", "GRAVE", "GREAT", "GREEN", "GREET", "GRIEF",
        "GROSS", "GROUP", "GROWN", "GUARD", "GUESS", "GUEST", "GUIDE",
        "GUILT", "HABIT", "HANDS", "HAPPY", "HARSH", "HEARD", "HEART",
        "HEAVY", "HENCE", "HORSE", "HOTEL", "HOURS", "HOUSE", "HUMAN",
        "HURRY", "IDEAL", "IMAGE", "IMPLY", "INDEX", "INNER", "INPUT",
        "ISSUE", "JOINT", "JUDGE", "JUICE", "KNOWN", "KNOCK", "LABEL",
        "LABOR", "LARGE", "LASER", "LATER", "LAUGH", "LAYER", "LEARN",
        "LEAST", "LEAVE", "LEGAL", "LEVEL", "LIGHT", "LIMIT", "LINKS",
        "LIVES", "LOCAL", "LOGIC", "LOOSE", "LOVER", "LOWER", "LUCKY",
        "LUNCH", "LYING", "MAGIC", "MAJOR", "MAKER", "MANOR", "MARCH",
        "MATCH", "MAYOR", "MEANS", "MEDIA", "MERCY", "MERIT", "METAL",
        "MIGHT", "MINOR", "MINUS", "MODEL", "MONEY", "MONTH", "MORAL",
        "MOTIF", "MOTOR", "MOUNT", "MOUTH", "MOVED", "MOVIE", "MUSIC",
        "NAIVE", "NERVE", "NEVER", "NIGHT", "NOISE", "NORTH", "NOTED",
        "NOVEL", "NURSE", "OCCUR", "OCEAN", "OFFER", "OFTEN", "ORDER",
        "OTHER", "OUGHT", "OUTER", "OWNED", "OWNER", "OXIDE", "PHASE",
        "PANIC", "PARTY", "PATCH", "PAUSE", "PEACE", "PENNY", "PHONE",
        "PHOTO", "PIECE", "PILOT", "PITCH", "PIZZA", "PLACE", "PLAIN",
        "PLANE", "PLANT", "PLATE", "PLAZA", "PLEAD", "PLUMB", "POEMS",
        "POINT", "POUND", "POWER", "PRESS", "PRICE", "PRIDE", "PRIME",
        "PRINT", "PRIOR", "PRIZE", "PROBE", "PROOF", "PROUD", "PROVE",
        "PROXY", "PSYCH", "PUPIL", "QUEEN", "QUERY", "QUEST", "QUEUE",
        "QUICK", "QUIET", "QUITE", "QUOTA", "QUOTE", "RADIO", "RAISE",
        "RALLY", "RANCH", "RANGE", "RAPID", "RATIO", "REACH", "READY",
        "REALM", "REIGN", "RELAX", "REPLY", "RIDER", "RIGHT", "RIGID",
        "RIVAL", "RIVER", "ROBIN", "ROBOT", "ROGER", "ROUGH", "ROUND",
        "ROUTE", "ROYAL", "RULED", "RURAL", "SADLY", "SAINT", "SCALE",
        "SCARE", "SCENE", "SCOPE", "SCORE", "SELLS", "SENSE", "SERVE",
        "SETUP", "SEVEN", "SHALL", "SHAME", "SHAPE", "SHARE", "SHARP",
        "SHEEP", "SHEER", "SHELF", "SHELL", "SHIFT", "SHINE", "SHOCK",
        "SHOES", "SHOOT", "SHORT", "SHOUT", "SHOWN", "SIGHT", "SINCE",
        "SIXTH", "SIXTY", "SIZED", "SKILL", "SKULL", "SLAVE", "SLEEP",
        "SLICE", "SLIDE", "SLOPE", "SMALL", "SMART", "SMELL", "SMILE",
        "SMOKE", "SOLAR", "SOLVE", "SORRY", "SOUND", "SOUTH", "SPACE",
        "SPARE", "SPEAK", "SPEED", "SPELL", "SPEND", "SPENT", "SPIKE",
        "SPLIT", "SPOKE", "SPORT", "SPRAY", "SQUAD", "STACK", "STAFF",
        "STAGE", "STAIN", "STAKE", "STALL", "STAMP", "STAND", "STARE",
        "START", "STATE", "STAYS", "STEAL", "STEAM", "STEEL", "STEEP",
        "STEER", "STERN", "STICK", "STIFF", "STILL", "STOCK", "STONE",
        "STOOD", "STORE", "STORM", "STORY", "STOVE", "STRAP", "STRAW",
        "STRAY", "STRIP", "STUCK", "STUDY", "STUFF", "STYLE", "SUGAR",
        "SUITE", "SUNNY", "SUPER", "SURGE", "SWEAR", "SWEEP", "SWEET",
        "SWIFT", "SWING", "SWORD", "SWORE", "STUCK", "TABLE", "TAKEN",
        "TASTE", "TEETH", "THEIR", "THEME", "THERE", "THESE", "THICK",
        "THIEF", "THING", "THINK", "THIRD", "THOSE", "THREE", "THREW",
        "THROW", "THUMB", "TIGHT", "TIMER", "TIRED", "TITLE", "TODAY",
        "TOKEN", "TOTAL", "TOUCH", "TOUGH", "TOWER", "TOXIC", "TRACE",
        "TRACK", "TRADE", "TRAIL", "TRAIN", "TRAIT", "TRASH", "TREAT",
        "TREND", "TRIAL", "TRIBE", "TRICK", "TRIED", "TROOP", "TRUCK",
        "TRULY", "TRUST", "TRUTH", "TUMOR", "TWICE", "TWIST", "ULTRA",
        "UNCLE", "UNDER", "UNION", "UNITE", "UNITY", "UNTIL", "UPPER",
        "UPSET", "URBAN", "USAGE", "USUAL", "VALID", "VALUE", "VIDEO",
        "VIGOR", "VIRAL", "VIRUS", "VISIT", "VITAL", "VIVID", "VOCAL",
        "VOICE", "VOTER", "WAGES", "WASTE", "WATCH", "WATER", "WEIGH",
        "WEIRD", "WHALE", "WHEAT", "WHEEL", "WHERE", "WHICH", "WHILE",
        "WHITE", "WHOLE", "WHOSE", "WIDER", "WIDTH", "WOMAN", "WOMEN",
        "WORLD", "WORRY", "WORSE", "WORST", "WORTH", "WOULD", "WOUND",
        "WRIST", "WRITE", "WRONG", "WROTE", "YIELD", "YOUNG", "YOUTH",
        # ── Government / org acronyms ──
        "CEO", "CFO", "COO", "SEC", "GDP", "USA", "IRS", "FBI", "CIA",
        "NASA", "NATO", "JOBS", "DEPT", "CORP", "GOVT", "EURO", "ASIA",
    }

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    def validate(self, ticker: str) -> bool:
        """Validate a single ticker. Returns True if it's a real stock."""
        ticker = ticker.upper().strip()

        # Layer 1: Exclusion list (instant)
        if ticker in self.EXCLUSION_LIST:
            logger.debug("[Validator] %s REJECTED — exclusion list", ticker)
            return False

        if not ticker:
            logger.debug("[Validator] %s REJECTED — empty", ticker)
            return False

        if len(ticker) > 5:
            logger.debug("[Validator] %s REJECTED — length %d", ticker, len(ticker))
            return False

        # Check cache
        if ticker in self._cache:
            return self._cache[ticker]

        # Layer 2: yFinance check
        try:
            stock = yf.Ticker(ticker)
            fi = stock.fast_info
            price = getattr(fi, "last_price", None)
            if price is None or price <= 0:
                logger.debug("[Validator] %s REJECTED — no price data", ticker)
                self._cache[ticker] = False
                return False

            logger.info(
                "[Validator] %s VALIDATED — price=$%.2f", ticker, price
            )
            self._cache[ticker] = True
            return True

        except Exception as e:
            logger.debug("[Validator] %s REJECTED — yfinance error: %s", ticker, e)
            self._cache[ticker] = False
            return False

    def validate_batch(self, tickers: list[str]) -> list[str]:
        """Validate multiple tickers, return only the valid ones."""
        valid = []
        for t in tickers:
            if self.validate(t):
                valid.append(t.upper().strip())
        logger.info(
            "[Validator] Batch: %d/%d valid",
            len(valid), len(tickers),
        )
        return valid
