// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @notice Version anchoring for ONE synthetic fund on a disposable local chain.
/// @dev Does not validate accounting, human consent, or real-world operations.
contract FundCheckpoint {
    address public publisher;
    uint256 public last;
    mapping(uint256 => bytes32) private points;
    event Recorded(uint256 indexed number, bytes32 digest);

    constructor(address testPublisher) {
        require(testPublisher != address(0), "zero publisher");
        publisher = testPublisher;
    }

    function record(uint256 number, bytes32 digest) external {
        require(msg.sender == publisher, "publisher only");
        require(number > last, "number must increase");
        require(digest != bytes32(0), "empty digest");
        points[number] = digest;
        last = number;
        emit Recorded(number, digest);
    }

    function get(uint256 number) external view returns (bool found, bytes32 digest) {
        digest = points[number];
        found = digest != bytes32(0);
    }
}
