from docmaker.semantic.model import Entity, SemanticModel, load_model, save_model


def _model(root: str, grain: str, *, reviewed: bool = False) -> SemanticModel:
    return SemanticModel(entities={"incident": Entity(root=root, grain=grain, reviewed=reviewed)})


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "model.yaml"
    save_model(_model("DMT.DMT_F_QOS_INC", "un incident déclaré"), path)

    loaded = load_model(path)
    assert loaded.entities["incident"].root == "DMT.DMT_F_QOS_INC"
    assert loaded.entities["incident"].grain == "un incident déclaré"


def test_load_missing_file_returns_empty_model(tmp_path):
    assert load_model(tmp_path / "absent.yaml").entities == {}


def test_save_merge_preserves_reviewed_entities(tmp_path):
    path = tmp_path / "model.yaml"
    save_model(_model("DMT.OLD", "revu par un humain", reviewed=True), path, merge=False)

    final = save_model(_model("DMT.NEW", "proposition régénérée"), path)

    assert final.entities["incident"].root == "DMT.OLD"  # la version revue l'emporte
    assert final.entities["incident"].grain == "revu par un humain"


def test_save_merge_replaces_non_reviewed_entities(tmp_path):
    path = tmp_path / "model.yaml"
    save_model(_model("DMT.OLD", "ancienne proposition"), path, merge=False)

    final = save_model(_model("DMT.NEW", "nouvelle proposition"), path)

    assert final.entities["incident"].root == "DMT.NEW"
