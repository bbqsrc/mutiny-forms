angular.module("joinForm", []);

function JoinCtrl($scope) {
    $scope.submit = function() {
        var prop, node,
            out = {};
        
        $(".ng-pristine").removeClass("ng-pristine").addClass("ng-dirty");

        for (prop in $scope) {
            if (/^[\$\_]/.test(prop) || prop == "this" || prop == "submit") {
                continue;
            }
            out[prop] = $scope[prop];
        }

        node = $("input.ng-invalid-required").first();
        if (node.length) {
            node.focus();
        } else {
            // Do ajax
            $.ajax(location.pathname, {
                method: "post",
                data: {"data": JSON.stringify(out)},
            }).fail(function() {
                $("#completed").find(".alert").removeClass('alert-info').addClass('alert-danger')
                    .html("For some reason, the form had an error. Please contact the <a href='mailto:membership@pirateparty.org.au'>Secretary</a> for assistance.")
            }).always(function(data) {
                $("form").addClass('hidden');
                $("#completed").removeClass('hidden');
            });
        }
    }

    $scope.membership_level = "full";
    $scope.payment_method = "paypal";
}
