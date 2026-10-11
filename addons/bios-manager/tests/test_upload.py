"""Upload → recognise → place, end to end through the API, in the sandbox."""
import hashlib
import io
import zipfile

import pytest
from conftest import DATA, DOLPHIN, DUCK_BIOS, HOME, MELON_TOML, PCSX2_BIOS, RA, RYU
from fastapi.testclient import TestClient

import identify

PS2_BYTES = b"fake ps2 bios " * 100
PS2_MD5 = hashlib.md5(PS2_BYTES).hexdigest()
PROD_KEYS = "header_key = " + "a" * 64 + "\nmaster_key_00 = " + "b" * 32 + "\n"


@pytest.fixture
def client(box, monkeypatch):
    # A known hash for fake bytes: the real table only holds real dumps.
    monkeypatch.setitem(identify.KNOWN, PS2_MD5,
                        {"system": "pcsx2", "size": len(PS2_BYTES), "names": ["ps2-0150e-20001228.bin"]})
    return TestClient(box.app)


def zip_of(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def upload(client, files: dict) -> list[dict]:
    resp = client.post("api/upload", files=[("files", (name, data)) for name, data in files.items()])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def by_file(results: list[dict]) -> dict:
    return {r["file"]: r for r in results}


def test_known_hash_lands_in_the_core_declared_dir(client):
    r = by_file(upload(client, {"whatever.bin": PS2_BYTES}))["whatever.bin"]
    assert r["outcome"] == "placed" and r["how"] == "md5"
    assert (PCSX2_BIOS / "ps2-0150e-20001228.bin").read_bytes() == PS2_BYTES


def test_zip_with_junk_and_a_nested_zip(client):
    inner = zip_of({"keys/prod.keys": PROD_KEYS})
    pack = zip_of({"README.txt": "hello", "cover.jpg": b"\xff\xd8", "ps2/bios.bin": PS2_BYTES, "switch.zip": inner})
    r = by_file(upload(client, {"pack.zip": pack}))
    assert r["pack.zip/README.txt"]["outcome"] == "ignored"
    assert r["pack.zip/cover.jpg"]["reason"].startswith("Readme")
    assert r["pack.zip/ps2/bios.bin"]["outcome"] == "placed"
    assert r["pack.zip/switch.zip/keys/prod.keys"]["outcome"] == "placed"
    assert (RYU / "system/prod.keys").read_text() == PROD_KEYS


def test_zip_slip_member_is_refused_and_nothing_escapes(client):
    pack = zip_of({"../../evil.bin": PS2_BYTES, "/abs/evil.bin": PS2_BYTES})
    results = upload(client, {"pack.zip": pack})
    assert all(r["outcome"] == "ignored" and r["reason"] == "Unsafe path inside the archive." for r in results)
    assert not list(HOME.rglob("evil.bin")) and not list(DATA.rglob("evil.bin"))


def test_7z_is_refused_with_a_way_out(client):
    r = upload(client, {"bios.7z": b"7z\xbc\xaf\x27\x1c" + b"\0" * 32})[0]
    assert r["outcome"] == "ignored" and "zip" in r["reason"]


def test_switch_keys_by_content_whatever_the_name(client):
    title = "0" * 32 + " = " + "1" * 32 + "\n"
    r = by_file(upload(client, {"my keys.txt": PROD_KEYS, "t.txt": title}))
    assert r["my keys.txt"]["outcome"] == "placed"
    assert (RYU / "system/title.keys").read_text() == title


def test_uninstalled_emulator_is_reported_not_written(client):
    r = upload(client, {"keys.txt": "0123456789abcdef0123456789abcdef # game\n"})[0]
    assert r["outcome"] == "ignored" and "not installed" in r["reason"]
    assert not (HOME / ".var/app/info.cemu.Cemu").exists()


def test_existing_files_same_replaced_kept(client):
    PCSX2_BIOS.mkdir(parents=True)
    target = PCSX2_BIOS / "ps2-0150e-20001228.bin"
    target.write_bytes(b"older dump")
    r = upload(client, {"a.bin": PS2_BYTES})[0]
    assert r["outcome"] == "replaced" and target.read_bytes() == PS2_BYTES
    backup = DATA / "addons/bios-manager/backups"
    assert [p.read_bytes() for p in backup.rglob("*.bin")] == [b"older dump"]
    assert upload(client, {"a.bin": PS2_BYTES})[0]["outcome"] == "same"


def test_verified_dump_is_not_replaced_by_an_unverified_one(client):
    (RYU / "system").mkdir(parents=True)
    # prod.keys has no fixed hash, so pretend the one on disk is a verified file.
    (RYU / "system/prod.keys").write_text("verified")
    identify.KNOWN[hashlib.md5(b"verified").hexdigest()] = {"system": "switch", "size": 8, "names": ["prod.keys"]}
    try:
        r = upload(client, {"prod.keys": PROD_KEYS})[0]
    finally:
        del identify.KNOWN[hashlib.md5(b"verified").hexdigest()]
    assert r["outcome"] == "kept" and (RYU / "system/prod.keys").read_text() == "verified"


def test_ps3_pup_is_staged_with_the_emulator_step_then_removable(client):
    r = upload(client, {"PS3UPDAT.PUP": b"SCEUF" + b"\0" * 100})[0]
    assert r["outcome"] == "waiting" and "--installfw" in r["command"]
    staged = client.get("api/status").json()["systems"]
    ps3 = next(s for s in staged if s["id"] == "rpcs3")
    assert ps3["staged"][0]["name"] == "PS3UPDAT.PUP"
    assert client.delete("api/staged", params={"path": str(HOME)}).status_code == 400
    assert client.delete("api/staged", params={"path": ps3["staged"][0]["path"]}).status_code == 200


def test_switch_firmware_set_is_staged_and_a_few_ncas_are_not(client):
    firmware = zip_of({f"{i:032x}.nca": b"nca" for i in range(40)} | {"0" * 31 + "f.cnmt.nca/00": b"meta"})
    r = next(x for x in upload(client, {"fw.zip": firmware}) if x.get("label") == "Switch firmware")
    assert r["outcome"] == "waiting" and "--install-firmware" in r["command"]
    staged = list((DATA / "addons/bios-manager/firmware/switch").rglob("*.nca"))
    assert len(staged) == 41
    few = upload(client, {"game.zip": zip_of({f"{i:032x}.nca": b"x" for i in range(3)})})
    assert few[0]["outcome"] == "ignored" and few[0]["file"] == "3 .nca files"


def test_melonds_configured_path_and_unconfigured_staging(client):
    MELON_TOML.parent.mkdir(parents=True)
    owner = HOME / "my bios/biosnds7.bin"
    MELON_TOML.write_text(f'[DS]\nBIOS7Path = "{owner}"\nBIOS9Path = ""\n')
    r = by_file(upload(client, {"bios7.bin": b"7" * 16384, "bios9.bin": b"9" * 4096}))
    assert r["bios7.bin"]["outcome"] == "placed" and owner.read_bytes() == b"7" * 16384
    assert r["bios9.bin"]["outcome"] == "waiting" and "ARM9" in r["bios9.bin"]["action"]


def test_existing_dsi_nand_is_never_replaced(client):
    MELON_TOML.parent.mkdir(parents=True)
    nand = HOME / "ds/nand.bin"
    nand.parent.mkdir()
    nand.write_bytes(b"saves inside")
    MELON_TOML.write_text(f'[DSi]\nNANDPath = "{nand}"\n')
    r = upload(client, {"dsi_nand.bin": b"n" * (201 << 20)})[0]
    assert r["outcome"] == "kept" and nand.read_bytes() == b"saves inside"


def test_melonds_path_outside_home_and_data_is_refused(client):
    MELON_TOML.parent.mkdir(parents=True)
    MELON_TOML.write_text('[DS]\nBIOS7Path = "/etc/bios7.bin"\n')
    r = upload(client, {"bios7.bin": b"7" * 16384})[0]
    assert r["outcome"] == "ignored" and "outside" in r["reason"]


def test_gamecube_ipl_needs_a_region_folder(client):
    ipl = b"\0" * (2 << 20)
    r = by_file(upload(client, {"EUR/IPL.bin": ipl, "IPL.bin": ipl}))
    assert r["EUR/IPL.bin"]["outcome"] == "placed" and (DOLPHIN / "GC/EUR/IPL.bin").is_file()
    assert r["IPL.bin"]["outcome"] == "ignored" and "Region" in r["IPL.bin"]["reason"]


def test_bios_archive_named_by_the_core_is_placed_whole(client):
    naomi = zip_of({"epr-21576h.ic27": b"rom"})
    r = upload(client, {"naomi.zip": naomi})[0]
    assert r["outcome"] == "placed" and (RA / "dc/naomi.zip").read_bytes() == naomi


def test_ps4_module_is_placed_but_a_ps3_firmware_piece_is_not(client):
    r = by_file(upload(client, {"libSceNgs2.sprx": b"ps4", "liblv2.sprx": b"ps3"}))
    assert r["libSceNgs2.sprx"]["outcome"] == "placed"
    assert (HOME / ".var/app/net.shadps4.shadPS4/data/shadPS4/sys_modules/libSceNgs2.sprx").read_bytes() == b"ps4"
    assert r["liblv2.sprx"]["outcome"] == "ignored"


def test_ps1_bios_by_content_keeps_its_name_and_pinned_ps2_note(client):
    ps1 = b"Sony Computer Entertainment Inc".ljust(512 * 1024, b"\0")
    ps2 = (b"RESET" + b"ROMVER").ljust(4 << 20, b"\0")
    r = by_file(upload(client, {"SCPH-9999.BIN": ps1, "other-ps2.bin": ps2}))
    assert (DUCK_BIOS / "SCPH-9999.BIN").read_bytes() == ps1
    assert "ps2-0150e-20001228.bin" in r["other-ps2.bin"]["note"]


def test_core_down_blocks_core_systems_only(client, box, monkeypatch):
    monkeypatch.setattr(box, "core_rows", lambda: None)
    r = by_file(upload(client, {"prod.keys": PROD_KEYS, "gba_bios.bin": b"g" * 16384}))
    assert r["prod.keys"]["outcome"] == "ignored" and "not answering" in r["prod.keys"]["reason"]
    assert (HOME / ".config/mgba/gba_bios.bin").is_file()


def test_oversized_upload_is_refused(client, box, monkeypatch):
    monkeypatch.setattr(box, "MAX_UPLOAD", 10)
    resp = client.post("api/upload", files=[("files", ("a.bin", b"x" * 100))])
    assert resp.status_code == 413


def test_status_counts_missing_and_says_what_needs_nothing(client):
    systems = {s["id"]: s for s in client.get("api/status").json()["systems"]}
    assert "lutris" not in systems
    assert systems["switch"]["missing_required"] == 1
    assert systems["wii"]["note"] and not systems["wii"]["files"]
    assert systems["gba"]["files"][0]["status"] == "absent"
