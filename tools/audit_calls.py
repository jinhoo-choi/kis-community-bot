"""모듈 간 호출 정합성 전수 검사.

최근 사고가 전부 이 계열이었다.
  #48  enrich 가 g.search(temperature=...) 로 부르는데 시그니처에 없어 TypeError
       → 실채널 발송 이틀 중단. 단위 테스트의 가짜 객체가 **kwargs 를 받아 못 잡았다.
  #45  워크플로 push 실패가 성공으로 끝남
  #34  주장 spec 이 남의 줄을 잡음

단위 테스트는 '동작' 을 보지 '연결' 을 보지 않는다. 여기서는 소스만 읽어
아래를 확인한다. 실행도 네트워크도 필요 없다.

  C1 src 모듈의 함수를 다른 파일이 부를 때, 그 함수가 실제로 있는가
  C2 넘기는 키워드 인자가 시그니처에 있는가 (**kwargs 를 받으면 통과)
  C3 넘기는 위치 인자 개수가 시그니처 범위 안인가
  C4 프로바이더 메서드(generate/search/generate_many)가 구현돼 있는가
"""
import ast
import glob
import importlib
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAIL = []


def fail(msg, where=""):
    FAIL.append((msg, where))


def _load(modname):
    try:
        return importlib.import_module(modname)
    except Exception as e:
        fail(f"import 실패: {modname} — {type(e).__name__}: {e}")
        return None


def _sig_ok(fn, call, where):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return
    params = sig.parameters
    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
    has_varargs = any(p.kind == p.VAR_POSITIONAL for p in params.values())
    names = {n for n, p in params.items()
             if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    for kw in call.keywords:
        if kw.arg and not has_kwargs and kw.arg not in names:
            fail(f"{where}: '{kw.arg}=' 인자가 시그니처에 없음 — {sig}")
    if not has_varargs:
        positional = [n for n, p in params.items()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if inspect.ismethod(fn) or getattr(fn, "__self__", None) is None:
            pass
        if len(call.args) > len(positional):
            fail(f"{where}: 위치 인자 {len(call.args)}개인데 받는 자리는 "
                 f"{len(positional)}개 — {sig}")


def audit_calls():
    """src/*.py, main.py 안의 'mod.func(...)' 호출을 실제 모듈과 대조한다."""
    files = ["main.py"] + sorted(glob.glob("src/**/*.py", recursive=True))
    for path in files:
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        # import 별칭 → 모듈명
        alias = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("src"):
                for a in n.names:
                    alias[a.asname or a.name] = f"{n.module}.{a.name}"
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("src"):
                        alias[a.asname or a.name] = a.name
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            base = n.func.value
            if not isinstance(base, ast.Name) or base.id not in alias:
                continue
            modname = alias[base.id]
            mod = _load(modname)
            if mod is None or inspect.isclass(mod):
                continue
            where = f"{path}:{n.lineno} {base.id}.{n.func.attr}()"
            fn = getattr(mod, n.func.attr, None)
            if fn is None:
                fail(f"{where}: {modname} 에 '{n.func.attr}' 없음", path)
                continue
            if inspect.isfunction(fn):
                _sig_ok(fn, n, where)


PROVIDER_METHODS = ("generate", "generate_many", "search", "available")


ROUTER_FNS = ("enricher", "writers", "judges")


def _is_router_call(node):
    """프로바이더를 돌려주는 호출인가.

    router.enricher() 처럼 모듈을 거치기도 하고, from ... import enricher 로
    받아 enricher() 로 바로 부르기도 한다(실제 enrich.py 가 그렇다). 둘 다 본다.
    writers()[...] / judges()[name] 같은 첨자도 벗겨서 판단한다.
    """
    subscripted = isinstance(node, ast.Subscript)
    n = node
    while isinstance(n, ast.Subscript):
        n = n.value
    if not isinstance(n, ast.Call):
        return False
    f = n.func
    name = f.attr if isinstance(f, ast.Attribute) else (
        f.id if isinstance(f, ast.Name) else "")
    if name == "enricher":
        return True
    # writers()/judges() 는 dict 를 돌려준다. 첨자를 거쳐야 프로바이더다.
    return name in ("writers", "judges") and subscripted


def audit_providers():
    """프로바이더 객체에 대고 부르는 메서드가 실제로 있는가.

    프로바이더는 모듈이 아니라 router 가 돌려주는 객체라, 위의 모듈 추적으로는
    잡히지 않는다. 함수 안에서 router.* 로 받은 변수를 따라가 그 변수에 대고
    부르는 메서드를 전부 확인한다. #48 사고가 정확히 이 경로였다.
    """
    from src.llm.claude import ClaudeProvider
    for m in PROVIDER_METHODS:
        if not hasattr(ClaudeProvider, m):
            fail(f"ClaudeProvider 에 '{m}' 없음")
    files = ["main.py"] + sorted(glob.glob("src/**/*.py", recursive=True))
    for path in files:
        tree = ast.parse(open(path, encoding="utf-8").read())
        for fn_node in [n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef)]:
            # 같은 이름이 함수마다 다른 뜻으로 쓰인다(generator 의 p 는 글 dict).
            # 이 함수 안에서만 보고, router 말고 다른 것도 대입되는 이름은 제외한다.
            body = [n for n in ast.walk(fn_node)
                    if not any(isinstance(a, ast.FunctionDef) and a is not fn_node
                               and n in ast.walk(a) for a in ast.walk(fn_node))]
            pvars, other = set(), set()
            for n in body:
                if isinstance(n, ast.Assign):
                    names = {t.id for t in n.targets if isinstance(t, ast.Name)}
                    (pvars if _is_router_call(n.value) else other).update(names)
                if isinstance(n, ast.For):
                    tgt = {n.target.id} if isinstance(n.target, ast.Name) else set()
                    it = n.iter.func if isinstance(n.iter, ast.Call) else None
                    if (isinstance(it, ast.Attribute) and _is_router_call(it.value)):
                        pvars |= tgt
                    else:
                        other |= tgt
            pvars -= other
            if not pvars:
                continue
            for n in body:
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name)
                        and n.func.value.id in pvars):
                    m = n.func.attr
                    where = f"{path}:{n.lineno} {n.func.value.id}.{m}()"
                    fn = getattr(ClaudeProvider, m, None)
                    if fn is None:
                        fail(f"{where}: ClaudeProvider 에 '{m}' 없음")
                    else:
                        _sig_ok(fn, n, where)


def audit_shared_safeguards():
    """새 경로가 기존 안전장치를 건너뛰지 않았는가.

    반복된 사고의 정체가 이것이다. 기능은 이미 있는데, 나중에 만든 경로가
    그 장치를 안 태운다.
      #145 search() 가 temperature 폴백(_call_with_temp)을 안 타 전건 실패
      #20  filters.check 가 kind 를 안 넘겨 앵커 주장이 '선정외주장' 으로 리젝
    장치마다 '통과해야 하는 관문' 을 여기 적어 두고, 우회로가 생기면 잡는다.
    """
    src = open("src/llm/claude.py", encoding="utf-8").read()
    tree = ast.parse(src)
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name == "_call_with_temp":
            continue
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "create"):
                continue
            if any(k.arg == "temperature" for k in n.keywords):
                fail(f"src/llm/claude.py:{n.lineno} {fn.name}(): temperature 를 "
                     f"직접 넘긴다 — SDK 폴백을 타려면 _call_with_temp 를 쓸 것")

    # 생성 프롬프트는 한 곳에서만 조립돼야 한다. 다른 곳에서 만들면 캐시 경계와
    # 강조·끝맺음 배정이 빠진 채로 호출된다.
    builders = set()
    for path in glob.glob("src/**/*.py", recursive=True) + ["main.py"]:
        t = ast.parse(open(path, encoding="utf-8").read())
        for n in ast.walk(t):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr.startswith("build_messages")):
                builders.add(n.func.attr)
    if builders - {"build_messages_v2"}:
        fail(f"생성 프롬프트 조립 경로가 여럿이다: {sorted(builders)}")

    # 본문 필터는 kind 별 앵커·근거 판정을 쓴다. kind 를 빼면 조용히 다른 결과가 난다.
    for path in ("src/generator.py", "src/template_reserve.py"):
        t = ast.parse(open(path, encoding="utf-8").read())
        for n in ast.walk(t):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "check"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id in ("filters", "_f2")):
                # kind 는 8번째 위치 인자이거나 kind= 키워드다
                has_kind = len(n.args) >= 8 or any(k.arg == "kind" for k in n.keywords)
                if not has_kind:
                    fail(f"{path}:{n.lineno} filters.check() 가 kind 를 넘기지 않는다 "
                         f"— 앵커 주장이 '선정외주장' 으로 리젝된다(#20)")


def main():
    print("=== 모듈 간 호출 정합성 ===")
    audit_calls()
    audit_providers()
    audit_shared_safeguards()
    for msg, *_ in FAIL:
        print(f"  FAIL {msg}")
    print(f"FAIL {len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
