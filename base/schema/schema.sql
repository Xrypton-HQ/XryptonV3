-- Xrypton Bot Database Schema (SQLite)
-- Tables in this file are created on startup by ``Xrypton._load_database``.
-- Individual cogs may also create their own tables in ``cog_load``.
-- The statements below mirror those so an empty database comes up fully
-- provisioned regardless of load order.

-- ───────────────────────── Core ─────────────────────────

-- Per-guild command prefix overrides
CREATE TABLE IF NOT EXISTS prefix (
    guild_id INTEGER PRIMARY KEY,
    prefix   TEXT NOT NULL
);

-- Users banned from invoking any commands
CREATE TABLE IF NOT EXISTS blacklist (
    user_id      INTEGER PRIMARY KEY,
    reason       TEXT,
    moderator_id INTEGER,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Aliases redirecting to canonical command names
CREATE TABLE IF NOT EXISTS aliases (
    guild_id INTEGER NOT NULL,
    alias    TEXT    NOT NULL,
    command  TEXT    NOT NULL,
    PRIMARY KEY (guild_id, alias)
);

-- Boost tracking (legacy logger used by ``on_message`` / lost-boost hook)
CREATE TABLE IF NOT EXISTS lost_boosters (
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    username      TEXT,
    discriminator TEXT,
    lost_at       TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

-- ───────────────────────── Fun ──────────────────────────

-- User vape tracking
CREATE TABLE IF NOT EXISTS vape (
    user_id INTEGER PRIMARY KEY,
    flavor  TEXT    NOT NULL,
    hits    INTEGER DEFAULT 0
);

-- Guild-specific blunt tracking (single active blunt per guild)
CREATE TABLE IF NOT EXISTS blunt (
    guild_id INTEGER PRIMARY KEY,
    user_id  INTEGER NOT NULL,
    hits     INTEGER DEFAULT 0,
    passes   INTEGER DEFAULT 0,
    members  TEXT    DEFAULT '[]'
);

-- Marriage tracking
CREATE TABLE IF NOT EXISTS marriages (
    user_id       INTEGER NOT NULL,
    partner_id    INTEGER NOT NULL,
    marriage_date TIMESTAMP,
    PRIMARY KEY (user_id, partner_id)
);

-- Family / adoption tracking
CREATE TABLE IF NOT EXISTS family (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    child_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, child_id)
);

-- WYR channel configuration
CREATE TABLE IF NOT EXISTS wyr_channels (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    rating     TEXT    DEFAULT 'pg13'
);

-- ─────────────────────── Information ────────────────────

-- Guild name change history
CREATE TABLE IF NOT EXISTS gnames (
    guild_id   INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User name change history
CREATE TABLE IF NOT EXISTS name_history (
    user_id    INTEGER NOT NULL,
    username   TEXT    NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Track boosters that left the server
CREATE TABLE IF NOT EXISTS boosters_lost (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    lasted_for TEXT,
    ended_at   TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

-- ─────────────────────── Moderation ──────────────────────

-- Guild-level moderation configuration (mod-log channel, jail, etc.)
CREATE TABLE IF NOT EXISTS mod (
    guild_id        INTEGER PRIMARY KEY,
    channel_id      INTEGER,
    jail_id         INTEGER,
    role_id         INTEGER,
    dm_enabled      BOOLEAN DEFAULT 1,
    dm_ban          TEXT,
    dm_kick         TEXT,
    dm_timeout      TEXT,
    dm_jail         TEXT,
    dm_role_add     TEXT,
    dm_role_remove  TEXT
);

-- Per-guild case counter
CREATE TABLE IF NOT EXISTS cases (
    guild_id INTEGER PRIMARY KEY,
    count    INTEGER DEFAULT 0
);

-- Moderation case history (one row per action)
CREATE TABLE IF NOT EXISTS moderation (
    guild_id     INTEGER NOT NULL,
    case_id      INTEGER NOT NULL,
    user_id      INTEGER,
    moderator_id INTEGER,
    action       TEXT,
    reason       TEXT,
    duration     INTEGER,
    role_id      INTEGER,
    timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, case_id)
);

-- Users currently jailed
CREATE TABLE IF NOT EXISTS jail (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    roles    TEXT,
    PRIMARY KEY (guild_id, user_id)
);

-- Permanently banned users
CREATE TABLE IF NOT EXISTS hardban (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- Forced nicknames
CREATE TABLE IF NOT EXISTS forcenick (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    nickname TEXT,
    PRIMARY KEY (guild_id, user_id)
);

-- Fake permissions granted to roles via the fakepermissions cog
CREATE TABLE IF NOT EXISTS fake_permissions (
    guild_id   INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    permission TEXT,
    PRIMARY KEY (guild_id, role_id)
);

-- Warn-action escalation thresholds
CREATE TABLE IF NOT EXISTS warn_actions (
    guild_id  INTEGER NOT NULL,
    threshold INTEGER NOT NULL,
    action    TEXT,
    duration  INTEGER,
    PRIMARY KEY (guild_id, threshold)
);

-- Users/roles immune from moderation
CREATE TABLE IF NOT EXISTS immune (
    guild_id  INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    role_id   INTEGER,
    type      TEXT,
    PRIMARY KEY (guild_id, entity_id, type)
);

-- Per-guild lock / invoke settings
CREATE TABLE IF NOT EXISTS settings (
    guild_id        INTEGER PRIMARY KEY,
    lock_role_id    INTEGER,
    lock_ignore_ids TEXT,
    invoke_ban      TEXT,
    invoke_kick     TEXT,
    invoke_timeout  TEXT,
    invoke_unban    TEXT,
    invoke_untimeout TEXT
);

-- ──────────────────────── Snipe ─────────────────────────

-- Recently deleted messages
CREATE TABLE IF NOT EXISTS snipe_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    user_name   TEXT,
    user_avatar TEXT,
    created_at  TIMESTAMP,
    deleted_at  TIMESTAMP,
    content     TEXT,
    attachments TEXT,
    stickers    TEXT
);
CREATE INDEX IF NOT EXISTS idx_snipe_messages_channel ON snipe_messages(channel_id);

-- Recently removed reactions
CREATE TABLE IF NOT EXISTS snipe_reactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    user_name  TEXT,
    removed_at TIMESTAMP,
    emoji      TEXT
);
CREATE INDEX IF NOT EXISTS idx_snipe_reactions_channel ON snipe_reactions(channel_id);

-- Recently edited messages
CREATE TABLE IF NOT EXISTS snipe_edits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL,
    channel_id     INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    user_name      TEXT,
    user_avatar    TEXT,
    before_content TEXT,
    after_content  TEXT,
    edited_at      TIMESTAMP,
    attachments    TEXT,
    stickers       TEXT
);
CREATE INDEX IF NOT EXISTS idx_snipe_edits_channel ON snipe_edits(channel_id);

-- Snipe filters (invites/links/words per guild)
CREATE TABLE IF NOT EXISTS snipe_filter (
    guild_id INTEGER PRIMARY KEY,
    invites  BOOLEAN DEFAULT 0,
    links    BOOLEAN DEFAULT 0,
    words    TEXT    DEFAULT '[]'
);

-- Users ignored by snipe
CREATE TABLE IF NOT EXISTS snipe_ignore (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- ────────────────────── Giveaway ────────────────────────

-- Active and historical giveaways
CREATE TABLE IF NOT EXISTS giveaway (
    guild_id       INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    channel_id     INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    prize          TEXT    NOT NULL,
    emoji          TEXT    NOT NULL,
    winners        INTEGER NOT NULL,
    ends_at        TIMESTAMP NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended          BOOLEAN NOT NULL DEFAULT 0,
    required_roles TEXT    DEFAULT '[]',
    bonus_roles    TEXT    DEFAULT '{}',
    PRIMARY KEY (guild_id, channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_giveaway_ends ON giveaway(ends_at);

-- Per-guild giveaway settings (bonus roles)
CREATE TABLE IF NOT EXISTS giveaway_settings (
    guild_id    INTEGER PRIMARY KEY,
    bonus_roles TEXT    DEFAULT '{}'
);

-- ─────────────────────── Roleplay ───────────────────────

-- Counts of roleplay actions between users
CREATE TABLE IF NOT EXISTS roleplay (
    user_id   INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    category  TEXT    NOT NULL,
    amount    INTEGER DEFAULT 1,
    PRIMARY KEY (user_id, target_id, category)
);

-- Rate-limit tracking for roleplay commands
CREATE TABLE IF NOT EXISTS roleplay_ratelimit (
    user_id   INTEGER PRIMARY KEY,
    count     INTEGER NOT NULL,
    timestamp TIMESTAMP NOT NULL
);