from pathlib import Path

from lantern_analysis.project import ClassT, Project


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_python_modules_under_a_source_root_resolve(tmp_path: Path) -> None:
    _write(tmp_path, "backend/app/__init__.py", "")
    _write(tmp_path, "backend/app/models.py", "class UserCreate:\n    email: str\n")
    _write(
        tmp_path,
        "backend/app/routes.py",
        "from app.models import UserCreate\n\n"
        "def create(user_in: UserCreate):\n    return user_in\n",
    )
    project = Project.load(
        tmp_path,
        {"python": ["backend/app/__init__.py", "backend/app/models.py", "backend/app/routes.py"]},
    )
    fn = project.functions["backend.app.routes:create"]
    assert project.resolve_annotation(fn, "UserCreate") == ClassT("backend.app.models:UserCreate")


def test_src_layout_resolves(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "")
    _write(tmp_path, "src/pkg/a.py", "def f():\n    return 1\n")
    _write(tmp_path, "src/pkg/b.py", "from pkg.a import f\n\ndef g():\n    return f()\n")
    project = Project.load(
        tmp_path, {"python": ["src/pkg/__init__.py", "src/pkg/a.py", "src/pkg/b.py"]}
    )
    g = project.functions["src.pkg.b:g"]
    from lantern_analysis.ir import Call, Return

    ret = g.body[0]
    assert isinstance(ret, Return) and isinstance(ret.value, Call)
    assert project.resolve_call(g, ret.value).fid == "src.pkg.a:f"
