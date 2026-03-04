import asyncio
from ib_insync import IB

async def test_connect_readonly():
    ib = IB()
    try:
        # Try to connect, with timeout
        await asyncio.wait_for(ib.connectAsync('127.0.0.1', 4002, clientId=2), timeout=3)
        
        # Try to get account code from different attributes
        print(f"Testing IB object attributes:")
        print(f"  - ib.isConnected(): {ib.isConnected()}")
        
        # Check for account code in different ways
        print(f"\nTrying different account code attributes:")
        
        # Method 1: accountSummary
        try:
            acc_summary = await ib.accountSummaryAsync()
            print(f"  ✓ accountSummary available: {len(list(acc_summary)) if acc_summary else 0} items")
            if acc_summary:
                print(f"    Sample: {list(acc_summary)[:1]}")
        except Exception as e:
            print(f"  ✗ accountSummary failed: {e}")
        
        # Method 2: positions
        try:
            positions = ib.positions()
            print(f"  ✓ positions: {len(positions)} items")
        except Exception as e:
            print(f"  ✗ positions failed: {e}")
        
        # Method 3: portfolio
        try:
            portfolio = ib.portfolio()
            print(f"  ✓ portfolio: {len(portfolio)} items")
        except Exception as e:
            print(f"  ✗ portfolio failed: {e}")
        
        # Try reqAccountSummary
        try:
            ib.reqAccountSummary(1, "All", "NetLiquidation")
            await asyncio.sleep(0.5)  # Give it time to receive data
            print(f"  → accountSummary data requested")
        except Exception as e:
            print(f"  ✗ reqAccountSummary failed: {e}")
            
    except asyncio.TimeoutError:
        print("Connection timeout")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
    finally:
        if ib.isConnected():
            await ib.disconnectAsync()

asyncio.run(test_connect_readonly())
