-- darktable's data.db as a fresh darktable creates it, for the R11 agent tests (no personal data).
-- Source: darktable src/common/database.c at commit 04477d32a00cc5abaea36d5bee084eb40c6eabb2 (the commit pinned in
-- darktable-android upstream/darktable): _create_data_schema (state of 27 Oct 2024, data version 10), then the
-- upgrade steps 10 -> 13 of _upgrade_data_schema_step that change tables (step 12: meta_data). The "data." prefix of
-- the statements is left out because the tests open data.db on its own, not attached.
CREATE TABLE db_info (key VARCHAR PRIMARY KEY, value VARCHAR);
INSERT INTO db_info (key, value) VALUES ('version', 13);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name VARCHAR, synonyms VARCHAR, flags INTEGER);
CREATE UNIQUE INDEX tags_name_idx ON tags (name);
CREATE TABLE styles (id INTEGER PRIMARY KEY, name VARCHAR, description VARCHAR, iop_list VARCHAR);
CREATE INDEX styles_name_index ON styles (name);
CREATE TABLE style_items (styleid INTEGER, num INTEGER, module INTEGER,
                          operation VARCHAR(256), op_params BLOB, enabled INTEGER,
                          blendop_params BLOB, blendop_version INTEGER,
                          multi_priority INTEGER, multi_name VARCHAR(256),
                          multi_name_hand_edited INTEGER);
CREATE INDEX IF NOT EXISTS style_items_styleid_index ON style_items (styleid);
CREATE TABLE presets (name VARCHAR, description VARCHAR, operation VARCHAR, op_version INTEGER, op_params BLOB,
                      enabled INTEGER, blendop_params BLOB, blendop_version INTEGER,
                      multi_priority INTEGER, multi_name VARCHAR(256),
                      multi_name_hand_edited INTEGER,
                      model VARCHAR, maker VARCHAR, lens VARCHAR, iso_min REAL, iso_max REAL,
                      exposure_min REAL, exposure_max REAL,
                      aperture_min REAL, aperture_max REAL, focal_length_min REAL,
                      focal_length_max REAL, writeprotect INTEGER,
                      autoapply INTEGER, filter INTEGER, def INTEGER, format INTEGER);
CREATE UNIQUE INDEX presets_idx ON presets (name, operation, op_version);
CREATE TABLE locations (tagid INTEGER PRIMARY KEY, type INTEGER, longitude REAL, latitude REAL, delta1 REAL, delta2 REAL,
                        ratio FLOAT, polygons BLOB, FOREIGN KEY(tagid) REFERENCES tags(id));
CREATE TABLE meta_data (key INTEGER PRIMARY KEY, tagname VARCHAR, name VARCHAR, internal INTEGER, visible INTEGER,
                        private INTEGER, display_order INTEGER);
CREATE UNIQUE INDEX meta_data_tagname_idx ON meta_data (tagname);
CREATE UNIQUE INDEX meta_data_name_idx ON meta_data (name);
