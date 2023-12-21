// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

import "forge-std/Test.sol";
import "../src/Bookie.sol";
import "forge-std/console.sol";
// import {Utils} from "./utils/Utils.sol";


contract BookieTest is Test {
    Bookie public bookie;
    address owner;
    address alice = address(0x1);
    address bob = address(0x2);

    string _sym = "1234";
    uint256 _amt = 1 ether;
    uint256 _init_eoa_balance = 10 ether;
    uint256 _safety_margin;
    uint256 _min_expiration;

    function setUp() public {
        owner = address(this);
        bookie = new Bookie();
        _safety_margin = bookie.BLOCK_SAFETY_MARGIN();

        vm.deal(alice, _init_eoa_balance);
        vm.deal(bob, _init_eoa_balance);
        vm.deal(owner, _init_eoa_balance);
        vm.roll(0);
    }

    function test_Deposit() public {
        vm.prank(alice);
        bookie.deposit{value: 0.5 ether}();
        assertEq(bookie.getSpendableBalance(alice), 0.5 ether);
    }
    function test_DepositTo() public {
        vm.prank(alice);
        bookie.depositTo{value: 0.5 ether}(bob);
        assertEq(bookie.getSpendableBalance(alice), 0 ether);
        assertEq(bookie.getSpendableBalance(bob), 0.5 ether);
    }

    function test_DepositTooMuch() public {
        vm.prank(alice);
        vm.expectRevert("Deposited too much");
        bookie.deposit{value: 5 ether}();
    }

    function test_RevertOverdrawBalance() public {
        vm.prank(alice);
        vm.expectRevert("Insufficient balance");
        bookie.withdraw(0.5 ether);
    }

    function testMakeBet() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        
        assertEq(bookie.getSpendableBalance(alice), 1 ether);
        assertEq(bookie.getSpendableBalance(bob), 1 ether);
        vm.roll(0);
        bookie.makeBet(alice, bob, "WEED", 1 ether, 69000, _safety_margin);
        
        assertEq(bookie.getSpendableBalance(alice), 0 ether);
        assertEq(bookie.getSpendableBalance(bob), 0 ether);
        assertEq(bookie.getLockedBalance(alice), 1 ether);
        assertEq(bookie.getLockedBalance(bob), 1 ether);

        console.log("%s", bookie.getSpendableBalance(owner));
    }

    
    function test_RevertRandoMakesBet() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        
        assertEq(bookie.getSpendableBalance(alice), 1 ether);
        assertEq(bookie.getSpendableBalance(bob), 1 ether);
        vm.prank(alice);
        vm.roll(0);
        vm.expectRevert("Not bookie");
        bookie.makeBet(alice, bob, "WEED", 1 ether, 69000, _safety_margin);
    }


    function test_BetExpiration() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        
        assertEq(bookie.getSpendableBalance(alice), 1 ether);
        assertEq(bookie.getSpendableBalance(bob), 1 ether);
        vm.roll(0);
        vm.expectRevert("bet expiration too soon");
        bookie.makeBet(alice, bob, "WEED", 1 ether, 69000, _safety_margin - 1);

        vm.roll(0);
        bookie.makeBet(alice, bob, "WEED", 1 ether, 69000, _safety_margin);

    }

    function test_SettleBetOver() public {
        uint256 bet_amount = 1 ether;
        uint256 expected_rake = 40000000000000000;

        vm.prank(alice);
        bookie.deposit{value: bet_amount}();
        vm.prank(bob);
        bookie.deposit{value: bet_amount}();
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        vm.roll(_safety_margin);
        bookie.settleBet(0, true);

        uint256 actual_rake = bookie.getSpendableBalance(owner);
        uint256 expected_win_amt = 2 * bet_amount - actual_rake;

        assertEq(actual_rake, expected_rake);

        assertEq(bookie.getSpendableBalance(alice), expected_win_amt);
        assertEq(bookie.getLockedBalance(alice), 0 ether);

        assertEq(bookie.getSpendableBalance(bob), 0 ether);
        assertEq(bookie.getLockedBalance(bob), 0 ether);

        // log rake
        console.log("raked: %s", bookie.getSpendableBalance(owner));
    }

    function test_SettleBetUnder() public {
        uint256 bet_amount = 1 ether;
        uint256 expected_rake = 40000000000000000;

        vm.prank(alice);
        bookie.deposit{value: bet_amount}();
        vm.prank(bob);
        bookie.deposit{value: bet_amount}();
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        vm.roll(_safety_margin);
        bookie.settleBet(0, false);

        uint256 actual_rake = bookie.getSpendableBalance(owner);
        uint256 expected_win_amt = 2 * bet_amount - actual_rake;

        assertEq(actual_rake, expected_rake);

        assertEq(bookie.getSpendableBalance(bob), expected_win_amt);
        assertEq(bookie.getLockedBalance(bob), 0 ether);

        assertEq(bookie.getSpendableBalance(alice), 0 ether);
        assertEq(bookie.getLockedBalance(alice), 0 ether);

        // log rake
        console.log("raked: %s", bookie.getSpendableBalance(owner));
    }

    function test_revertRandoSettlesBet() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        vm.roll(_safety_margin);
        vm.prank(alice);
        vm.expectRevert("Not bookie");
        bookie.settleBet(0, true);
    }

    function test_SettleTooSoon() public {
        uint256 bet_amount = 1 ether;

        vm.prank(alice);
        bookie.deposit{value: bet_amount}();
        vm.prank(bob);
        bookie.deposit{value: bet_amount}();
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        vm.roll(_safety_margin - 1);
        vm.expectRevert("cannot settle bet before expiration");
        bookie.settleBet(0, true);
    }

    function test_BookieInvalidateBet() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        bookie.bookieInvalidateBet(0);
        
        assertEq(address(alice).balance, _init_eoa_balance);
        assertEq(address(bob).balance, _init_eoa_balance);
    }

    function test_UserInvalidateStaleBet() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        assertEq(bookie.getLockedBalance(alice), 1 ether);
        assertEq(bookie.getLockedBalance(bob), 1 ether);
        assertEq(bookie.getSpendableBalance(alice), 0 ether);
        assertEq(bookie.getSpendableBalance(bob), 0 ether);
        vm.roll(bookie.INVALIDATION_WINDOW() + _safety_margin);
        vm.prank(alice);
        bookie.invalidateStaleBet(0);

        assertEq(bookie.getSpendableBalance(alice), 1 ether);
        assertEq(bookie.getSpendableBalance(bob), 1 ether);
        assertEq(bookie.getLockedBalance(alice), 0 ether);
        assertEq(bookie.getLockedBalance(bob), 0 ether);
        
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        vm.roll(bookie.INVALIDATION_WINDOW() + _safety_margin - 1);
        vm.prank(alice);
        vm.expectRevert("bet is still within bookie control window");
        bookie.invalidateStaleBet(1);
    }

    function test_cantDoubleSettle() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        vm.roll(_safety_margin);
        bookie.settleBet(0, true);
        vm.expectRevert("bet has already been settled or invalidated");
        bookie.settleBet(0, true);
    }

    function test_cantInvalidateSettled() public {
        vm.prank(alice);
        bookie.deposit{value: 1 ether}();
        vm.prank(bob);
        bookie.deposit{value: 1 ether}();
        
        vm.roll(0);
        bookie.makeBet(alice, bob, "BONK", 1 ether, 69, _safety_margin);
        vm.roll(_safety_margin);
        bookie.settleBet(0, false);
        vm.roll(_safety_margin + bookie.INVALIDATION_WINDOW());
        vm.prank(alice);
        vm.expectRevert("cannot invalidate inactive bet");
        bookie.invalidateStaleBet(0);
    }

}

