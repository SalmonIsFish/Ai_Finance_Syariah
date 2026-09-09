import pytest
import ast
from pathlib import Path

def test_p4a_no_p3_execution_imports():
    """
    AST Static Check to assert that P4A does not import live execution 
    or mutation functions from P3/legacy systems.
    """
    p4a_files = [
        "backend/p4_historical_sc.py",
        "backend/p4_identity.py",
        "backend/p4_market_data.py",
        "backend/pure_risk.py",
        "backend/p4_schema.py"
    ]
    
    prohibited_modules = {
        "alpaca",
        "moomoo",
        "backend.paper_execution",
        "backend.p3_portfolio_engine",
        "p3_portfolio_engine",
        "backend.sc_admin_cli",
        "fastapi" # We shouldn't be defining routes in pure foundational layers
    }
    
    for file_path in p4a_files:
        path = Path(file_path)
        assert path.exists(), f"Expected Phase 4A file missing: {file_path}"
            
        tree = ast.parse(path.read_text(encoding="utf-8"))
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    for prohibited in prohibited_modules:
                        assert not name.name.startswith(prohibited), f"Prohibited import {name.name} found in P4A {file_path}!"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for prohibited in prohibited_modules:
                        assert not node.module.startswith(prohibited), f"Prohibited import {node.module} found in P4A {file_path}!"

def test_p3_no_p4_dependencies():
    """
    Assert P3 does NOT depend on P4, EXCEPT for the explicitly shared pure risk code.
    This proves P3 isn't entangled with P4 backtesting logic.
    """
    path = Path("backend/p3_decision_engine.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("backend.p4_"):
                pytest.fail(f"P3 code must not import P4 modules! Found: {node.module}")

