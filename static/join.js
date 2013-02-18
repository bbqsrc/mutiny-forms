angular.module("joinForm", []);

function JoinCtrl($scope) {
    $scope.submit = function() {
        var prop,
            out = {};
        for (prop in $scope) {
            if (/^[\$\_]/.test(prop)) {
                continue;
            }
            out[prop] = $scope[prop];
        }
        console.log(out);
    }

    $scope.membershipType = "full";
}
