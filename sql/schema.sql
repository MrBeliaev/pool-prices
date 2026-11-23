CREATE TABLE IF NOT EXISTS dexes (
    id              SERIAL PRIMARY KEY,
    name            TEXT   NOT NULL,
    protocol        TEXT   NOT NULL CHECK (protocol IN ('uniswap_v2', 'uniswap_v3', 'uniswap_v4')),
    chain_id        BIGINT NOT NULL CHECK (chain_id > 0),
    factory_address TEXT   NOT NULL,
    quoter_address  TEXT,
    fee             INTEGER CHECK (fee >= 0 AND fee < 1000000),
    UNIQUE (chain_id, factory_address)
    CHECK ((protocol = 'uniswap_v2') = (fee IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS tokens (
    id       SERIAL   PRIMARY KEY,
    chain_id BIGINT   NOT NULL CHECK (chain_id > 0),
    address  TEXT     NOT NULL,
    symbol   TEXT     NOT NULL,
    decimals SMALLINT NOT NULL CHECK (decimals BETWEEN 0 AND 36),
    UNIQUE (chain_id, address)
);

CREATE TABLE IF NOT EXISTS pools (
    id      SERIAL  PRIMARY KEY,
    dex_id  INTEGER NOT NULL REFERENCES dexes (id),
    address TEXT    NOT NULL,
    fee     INTEGER NOT NULL CHECK (fee >= 0 AND fee < 1000000),
    UNIQUE (dex_id, address)
);

CREATE OR REPLACE FUNCTION pools_fill_fee() RETURNS trigger AS $$
BEGIN
    IF NEW.fee IS NULL THEN
        SELECT fee INTO NEW.fee FROM dexes WHERE id = NEW.dex_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pools_fill_fee ON pools;
CREATE TRIGGER pools_fill_fee BEFORE INSERT ON pools
    FOR EACH ROW EXECUTE FUNCTION pools_fill_fee();

CREATE TABLE IF NOT EXISTS pool_tokens (
    pool_id  INTEGER  NOT NULL REFERENCES pools (id) ON DELETE CASCADE,
    token_id INTEGER  NOT NULL REFERENCES tokens (id),
    position SMALLINT NOT NULL CHECK (position >= 0),
    PRIMARY KEY (pool_id, token_id),
    UNIQUE (pool_id, position)
);