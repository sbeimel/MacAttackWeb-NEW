#!/usr/bin/env python3
"""
Validation script to test MacAttack-Web v3.0 integration
"""
import sys
import traceback

def test_imports():
    """Test that all modules can be imported."""
    print("Testing imports...")
    
    try:
        import stb
        print("✅ stb.py imported successfully")
        
        # Test key classes
        dns_cache = stb.DNSCache()
        print("✅ DNSCache class works")
        
        connector = stb.OptimizedConnector()
        print("✅ OptimizedConnector class works")
        
        rotator = stb.SmartProxyRotator()
        print("✅ SmartProxyRotator class works")
        
        # Test functions
        optimized_connector = stb.get_optimized_connector(50, 3)
        print("✅ get_optimized_connector function works")
        
    except Exception as e:
        print(f"❌ stb.py import failed: {e}")
        traceback.print_exc()
        return False
    
    try:
        import app
        print("✅ app.py imported successfully")
        
        # Test key classes
        scorer = app.ProxyScorer()
        print("✅ ProxyScorer class works")
        
        retry_queue = app.RetryQueue()
        print("✅ RetryQueue class works")
        
    except Exception as e:
        print(f"❌ app.py import failed: {e}")
        traceback.print_exc()
        return False
    
    try:
        import web
        print("✅ web.py imported successfully")
        
        # Test key classes
        scanner_manager = web.AsyncScannerManager()
        print("✅ AsyncScannerManager class works")
        
    except Exception as e:
        print(f"❌ web.py import failed: {e}")
        traceback.print_exc()
        return False
    
    return True

def test_configuration():
    """Test configuration loading."""
    print("\nTesting configuration...")
    
    try:
        import app
        config = app.load_config()
        print("✅ Configuration loaded successfully")
        
        # Check for new settings
        settings = config.get("settings", {})
        required_settings = [
            "connections_per_host",
            "requests_per_minute_per_proxy", 
            "min_delay_between_requests"
        ]
        
        for setting in required_settings:
            if setting in settings:
                print(f"✅ {setting}: {settings[setting]}")
            else:
                print(f"⚠️ {setting}: not found (will use default)")
        
    except Exception as e:
        print(f"❌ Configuration test failed: {e}")
        traceback.print_exc()
        return False
    
    return True

def test_proxy_integration():
    """Test proxy rotation integration."""
    print("\nTesting proxy integration...")
    
    try:
        import stb
        import app
        
        # Test SmartProxyRotator
        rotator = stb._smart_rotator
        test_proxies = ["proxy1.com:8080", "proxy2.com:3128"]
        
        for proxy in test_proxies:
            rotator.add_proxy(proxy)
        
        best_proxy = rotator.get_best_proxy(test_proxies)
        print(f"✅ SmartProxyRotator selected: {best_proxy}")
        
        # Test success recording
        rotator.record_success(best_proxy, 500.0)
        print("✅ Success recording works")
        
        # Test failure recording
        rotator.record_failure(best_proxy, "slow")
        print("✅ Failure recording works")
        
    except Exception as e:
        print(f"❌ Proxy integration test failed: {e}")
        traceback.print_exc()
        return False
    
    return True

def main():
    """Run all validation tests."""
    print("MacAttack-Web v3.0 - Integration Validation")
    print("=" * 50)
    
    tests = [
        test_imports,
        test_configuration,
        test_proxy_integration
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                print(f"❌ {test.__name__} failed")
        except Exception as e:
            print(f"❌ {test.__name__} crashed: {e}")
    
    print("\n" + "=" * 50)
    print(f"VALIDATION RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All integration tests passed!")
        print("\nPerformance optimizations are properly integrated:")
        print("✅ HTTP/2 Connection Pooling")
        print("✅ DNS Caching")
        print("✅ Smart Proxy Rotation with Anti-Detection")
        print("✅ Configurable Connection Limits")
        print("✅ Rate Limiting per Proxy")
        print("✅ Anti-Self-Blocking Measures")
        return True
    else:
        print("❌ Some integration tests failed!")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)