#!/usr/bin/env python3
"""
Simple import test for MacAttack-Web v3.0
"""

def test_stb_import():
    """Test stb.py import"""
    try:
        import stb
        print("✅ stb.py imported successfully")
        
        # Test key classes
        dns_cache = stb.DNSCache()
        print("✅ DNSCache instantiated")
        
        connector = stb.OptimizedConnector()
        print("✅ OptimizedConnector instantiated")
        
        rotator = stb.SmartProxyRotator()
        print("✅ SmartProxyRotator instantiated")
        
        return True
    except Exception as e:
        print(f"❌ stb.py import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_app_import():
    """Test app.py import"""
    try:
        import app
        print("✅ app.py imported successfully")
        return True
    except Exception as e:
        print(f"❌ app.py import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_web_import():
    """Test web.py import"""
    try:
        import web
        print("✅ web.py imported successfully")
        return True
    except Exception as e:
        print(f"❌ web.py import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Testing imports...")
    
    tests = [test_stb_import, test_app_import, test_web_import]
    passed = 0
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print(f"Results: {passed}/{len(tests)} imports successful")
    
    if passed == len(tests):
        print("🎉 All imports working correctly!")
    else:
        print("❌ Some imports failed")